#!/usr/bin/env python3
"""Run Senior 4 V1 source-only gates without OCI or host mutation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
INFRA = ROOT / "infrastructure"
ENV = INFRA / "opentofu/environments/v1-live"
SCHEMA = ROOT / "contracts/infrastructure/host-bundle-v1.schema.json"


def run(
    argv: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
    capture: bool = False, input_text: str | None = None,
) -> str:
    print("+", " ".join(argv))
    completed = subprocess.run(
        argv, cwd=cwd, env=env, text=True, input=input_text,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stdout or "") + (completed.stderr or "")
        raise AssertionError(detail.strip() or f"command failed: {argv}")
    return (completed.stdout or "").strip()


def source_encryption() -> str:
    return '''key_provider "pbkdf2" "source_validation" {
  passphrase = "source-validation-only-not-live-20260718"
}
method "aes_gcm" "source_validation" {
  keys = key_provider.pbkdf2.source_validation
}
state { method = method.aes_gcm.source_validation }
plan { method = method.aes_gcm.source_validation }
'''


def validate_json_and_yaml() -> None:
    for path in sorted(INFRA.rglob("*.json")) + sorted((ROOT / "contracts/infrastructure").glob("*.json")) + sorted((ROOT / "contracts/deployment").glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
    yaml.safe_load((INFRA / "otel/otelcol-v1.yaml").read_text(encoding="utf-8"))


def validate_shell_and_python() -> None:
    bash = shutil.which("bash")
    if not bash:
        raise AssertionError("Bash is required for source shell syntax checks")
    for path in sorted((INFRA / "bin").iterdir()) + sorted((INFRA / "deployment").glob("*.sh")):
        if path.is_file() and path.read_bytes().startswith(b"#!/usr/bin/env bash"):
            run([bash, "-n", path.as_posix()])
    python_files = [path for path in INFRA.rglob("*.py") if ".terraform" not in path.parts]
    python_files.extend(
        path for path in (INFRA / "bin").iterdir()
        if path.is_file() and path.read_bytes().startswith(b"#!/usr/bin/env python3")
    )
    run([sys.executable, "-m", "py_compile", *map(str, sorted(set(python_files)))])


def temporary_opentofu() -> tuple[Path, dict[str, str]]:
    temporary = Path(tempfile.mkdtemp(prefix="liqi-v1-tofu-source-"))
    copy = temporary / "opentofu"
    shutil.copytree(INFRA / "opentofu", copy)
    backend = copy / "environments/v1-live/backend.tf"
    backend.write_text(
        backend.read_text(encoding="utf-8").replace('  backend "pg" {}\n\n', ''),
        encoding="utf-8",
        newline="\n",
    )
    main = copy / "environments/v1-live/main.tf"
    main.write_text(main.read_text(encoding="utf-8").replace('abspath("${path.module}/../../..")', json.dumps(INFRA.as_posix())), encoding="utf-8", newline="\n")
    environment = os.environ.copy()
    environment["TF_ENCRYPTION"] = source_encryption()
    return copy / "environments/v1-live", environment


def validate_opentofu_and_cloud_init() -> None:
    run(["tofu", "fmt", "-check", "-recursive", str(INFRA / "opentofu")])
    temp_env, environment = temporary_opentofu()
    try:
        run(["tofu", "init", "-backend=false", "-input=false"], cwd=temp_env, env=environment)
        run(["tofu", "validate"], cwd=temp_env, env=environment)
        size_output = run(
            ["tofu", "console", "-var-file=terraform.tfvars.example"],
            cwd=temp_env,
            env=environment,
            capture=True,
            input_text="length(base64gzip(local.cloud_init_user_data))\n",
        )
        size = int(size_output.splitlines()[-1])
    finally:
        shutil.rmtree(temp_env.parents[2], ignore_errors=True)
    if size > 16384:
        raise AssertionError(f"compressed OCI user_data exceeds 16 KiB: {size}")
    print(f"validated compressed OCI user_data size: {size} bytes")


def quota(path: Path, key: str) -> int:
    match = re.search(rf"(?m)^{re.escape(key)}=([0-9]+)([MG%]?)$", path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(f"missing {key} in {path}")
    value, suffix = int(match.group(1)), match.group(2)
    if suffix == "G":
        return value * 1024
    return value


def validate_static_policy() -> None:
    systemd = INFRA / "systemd"
    parent_cpu = quota(systemd / "liqi-platform.slice", "CPUQuota")
    parent_mem = quota(systemd / "liqi-platform.slice", "MemoryMax")
    v1_children = ["liqi-beam.slice", "liqi-database.slice", "liqi-edge.slice", "liqi-telemetry.slice"]
    v1_cpu = sum(quota(systemd / name, "CPUQuota") for name in v1_children)
    v1_mem = sum(quota(systemd / name, "MemoryMax") for name in v1_children)
    v0_runtime_cpu = quota(systemd / "liqi-platform-runtime.slice", "CPUQuota")
    v0_runtime_mem = quota(systemd / "liqi-platform-runtime.slice", "MemoryMax")
    if (parent_cpu, parent_mem) != (300, 20480) or v1_cpu != 300 or v1_mem > parent_mem:
        raise AssertionError(f"V1 slices violate 3 OCPU/20 GiB ceiling: parent={(parent_cpu,parent_mem)} children={(v1_cpu,v1_mem)}")
    if v0_runtime_cpu > parent_cpu or v0_runtime_mem > parent_mem:
        raise AssertionError("disabled V0 compatibility slice exceeds the shared parent ceiling")
    beam = (systemd / "liqi-beam.service").read_text(encoding="utf-8")
    if "MemoryDenyWriteExecute" in beam or "User=liqi-beam" not in beam or "NoNewPrivileges=yes" not in beam:
        raise AssertionError("BEAM unit hardening/JIT compatibility changed")

    module_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((INFRA / "opentofu/modules/oci-live-v1").glob("*.tf")))
    if "oci_vault_secret" in module_text or 'port = 22' in module_text or "enable_admin_ssh" in module_text:
        raise AssertionError("source introduces secret-in-state or SSH ingress")
    for forbidden in ("oci_objectstorage_bucket", "Object Storage bucket", "object_storage_bucket"):
        if forbidden in module_text:
            raise AssertionError(f"V1 OCI module retains forbidden Object Storage dependency: {forbidden}")
    for token in (
        "oci_core_nat_gateway", "oci_core_service_gateway",
        "oci_network_load_balancer_network_load_balancer",
        "oci_network_load_balancer_listener", "oci_network_load_balancer_backend_set",
        "10.42.20.100/32", "10.42.20.109/32", "Compute Instance Run Command",
        'assign_public_ip = "false"', 'prohibit_public_ip_on_vnic = true',
        'resource "oci_core_subnet" "private_host"',
        'resource "oci_core_instance" "host"',
        'target_id                = oci_core_instance.host.id',
        'instance.id = \'${var.retained_fallback_instance_ocid}\'',
        'tcp_idle_timeout         = 1800',
        'is_offline               = !var.public_backend_enabled',
        'prevent_destroy = true',
        'outbound_internet_path = "nat-gateway"', 'oracle_services_path   = "service-gateway"',
    ):
        if token not in module_text:
            raise AssertionError(f"V1 private-host/NLB/Bastion source is missing {token}")
    if "management_wireguard_peer_cidr" in module_text or "management_tunnel_egress" in module_text:
        raise AssertionError("V1 source retains superseded WireGuard management assumptions")
    required_assignments = {
        "http": "80",
        "https": "443",
        "ocpus": "4",
        "memory_gib": "24",
        "data_volume_gib": "130",
    }
    for name, value in required_assignments.items():
        if not re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*{re.escape(value)}\s*$", module_text):
            raise AssertionError(f"missing capacity/network invariant: {name}={value}")
    for token in (
        'a1-target = {',
        'shape                    = "VM.Standard.A1.Flex"',
        'architecture             = "aarch64"',
        'target_triple            = "aarch64-unknown-linux-gnu"',
        'boot_volume_gib          = 50',
        'combined_storage_gib     = 180',
        'cost_classification      = "free-trial-only"',
        'e5-temporary = {',
        'shape                    = "VM.Standard.E5.Flex"',
        'architecture             = "x86_64"',
        'target_triple            = "x86_64-unknown-linux-gnu"',
        'boot_volume_gib          = 200',
        'combined_storage_gib     = 330',
        'cost_classification      = "paid-approved"',
        'migration_target_profile = "a1-target"',
        'var.operation_mode == "plan"',
        'var.capacity_profile == "e5-temporary"',
        'timeadd(timestamp(), "2160h")',
        'var.fallback_desired_state == "STOPPED"',
        'var.acknowledge_public_cutover',
    ):
        if token not in module_text:
            raise AssertionError(f"V1 A1/E5 profile or expiry guard is missing {token}")
    cost_manifest = json.loads((ENV / "cost-classification.json").read_text(encoding="utf-8"))
    compute_cost = next(item for item in cost_manifest["resources"] if item["resource"] == "VM.Standard.A1.Flex 4 OCPU / 24 GiB")
    if compute_cost.get("classification") != "free-trial-only" or compute_cost.get("apply_allowed_under_current_approval") is not False:
        raise AssertionError("V1 A1 4/24 cost classification is not fail-closed")
    e5_cost = next(item for item in cost_manifest["resources"] if item["resource"] == "VM.Standard.E5.Flex 4 OCPU / 24 GiB temporary bridge")
    if e5_cost.get("classification") != "paid-approved" or e5_cost.get("apply_allowed_under_current_approval") is not True:
        raise AssertionError("temporary E5 cost classification or approval boundary is incorrect")
    retained_compute_cost = next(item for item in cost_manifest["resources"] if item["resource"] == "retained primary and stopped recovery compute")
    if retained_compute_cost.get("classification") != "paid-approved-retained-capacity" or retained_compute_cost.get("apply_allowed_under_current_approval") is not True:
        raise AssertionError("retained-compute capacity and approval classification is not explicit")
    if cost_manifest.get("documented_always_free_a1") != {"ocpus_total": 2, "memory_gib_total": 12}:
        raise AssertionError("V1 cost manifest does not pin the current documented Always Free A1 limit")

    backend = (ENV / "backend.tf").read_text(encoding="utf-8")
    if 'backend "pg" {}' not in backend or 'backend "s3"' in backend:
        raise AssertionError("V1 live must use the self-hosted PostgreSQL pg backend")
    forbidden_backend_tokens = ("AWS_SHARED_CREDENTIALS_FILE", "use_lockfile", "compat.objectstorage")
    management_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((INFRA / "management/state-postgres").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    )
    for token in (
        "sslmode=verify-full", "postgresql-advisory-locks", "state-backend-evidence-v1",
        "WITH SET TRUE, INHERIT FALSE", "REVOKE %I FROM %I", "protect_file",
        "state runtime role must never be superuser or replication-capable",
        "ALTER ROLE %I LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD",
        "count(*)=1", "pg_get_userbyid(nspowner)=:'r'",
        "STATE_FINALIZE_SERVICE", "protected local bootstrap superuser",
        "ALTER SCHEMA %I OWNER TO %I", "ALTER TABLE %I.states OWNER TO %I",
        "ALTER SEQUENCE public.global_states_id_seq OWNER TO %I",
        "state admin cannot SET ROLE to the runtime identity",
        "REVOKE %I FROM %I", "unexpected Windows principal",
    ):
        if token not in management_text:
            raise AssertionError(f"self-hosted state provider is missing {token}")
    all_live_state_text = backend + management_text + (INFRA / "deployment/plan_v1_live.sh").read_text(encoding="utf-8")
    for token in forbidden_backend_tokens:
        if token in all_live_state_text:
            raise AssertionError(f"V1 state path retains forbidden S3 dependency: {token}")
    caddy_fail = (INFRA / "caddy/Caddyfile.fail-closed").read_text(encoding="utf-8")
    caddy_live = (INFRA / "caddy/Caddyfile.v1-live.tftpl").read_text(encoding="utf-8")
    if 'respond "LIQI edge is staged but traffic is not enabled" 503' not in caddy_fail:
        raise AssertionError("staged Caddy configuration is not fail-closed")
    for token in (
        "admin off",
        "${backend_address}",
        "max_size ${request_body_limit_bytes}",
        "max_header_size ${header_limit_bytes}",
        "request>headers>X-Liqi-Probe-Token delete",
    ):
        if token not in caddy_live:
            raise AssertionError(f"live Caddy template missing {token}")
    artifact_source = "\n".join((INFRA / path).read_text(encoding="utf-8") for path in (
        "deployment/build_host_bundle.py",
        "deployment/archive_host_bundle.py",
        "bin/liqi-install-host-bundle",
    ))
    for forbidden in ("oci os object", "--namespace-name", "--bucket-name", "object_prefix"):
        if forbidden in artifact_source:
            raise AssertionError(f"V1 artifact path retains Object Storage dependency: {forbidden}")
    adoption_source = "\n".join((INFRA / path).read_text(encoding="utf-8") for path in (
        "deployment/discover_v1_live_adoption.py", "deployment/adopt_v1_live_state.py",
        "deployment/plan_v1_live.sh", "deployment/apply_v1_live.sh",
        "validation/validate_adoption_result.py", "validation/validate_pre_apply_readiness.py", "validation/validate_v1_plan.py",
    ))
    for token in (
        "oci_mutation_performed", "state_mutation_performed", "adopt-existing",
        "adoption plan forbids delete/replacement", "adoption_result_sha256",
        "pre_apply_readiness_sha256", "pre-apply readiness digest mismatch",
        "plan-result-v1.schema.json", "apply-result-v1.schema.json",
        "OCI live output identity does not match approved apply",
        "approved apply requires an adopt-existing plan", "e5-temporary",
    ):
        if token not in adoption_source:
            raise AssertionError(f"E5 adoption source is missing fail-closed token {token}")
    for token in ("independent-management-storage", "operator-staged-local", "/var/lib/liqi/incoming/host"):
        if token not in artifact_source:
            raise AssertionError(f"V1 artifact provider is missing {token}")

    provider_requirements = {
        INFRA / "deployment/authorize_native_artifact.py": (
            "native/scripts/prepare_deployment_manifest.py",
            "native/scripts/verify_deployment_manifest.py",
            "native-authorization-result-v1.schema.json",
        ),
        INFRA / "deployment/prepare_mix_deployment.py": (
            "contracts/runtime/runtime-artifact-result-v1.schema.json",
            "contracts/database/migration-readiness-v1.schema.json",
            "contracts/native/native-artifact-v1.schema.json",
            "native/scripts/verify_deployment_manifest.py",
        ),
        INFRA / "deployment/stage_mix_release.py": (
            "native/scripts/verify_deployment_manifest.py",
            "runtime-config-v1.schema.json",
            "migration-readiness-v1.schema.json",
        ),
    }
    for provider_path, required_tokens in provider_requirements.items():
        provider_text = provider_path.read_text(encoding="utf-8")
        for token in required_tokens:
            if token not in provider_text:
                raise AssertionError(f"{provider_path} does not consume committed provider seam {token}")
    deployment_native = json.loads((ROOT / "contracts/deployment/native-artifact-v1.schema.json").read_text(encoding="utf-8"))
    expected_native_fields = {
        "schema_version", "artifact_id", "git_sha", "artifact", "load", "safety",
        "compatibility_adapter", "provider_contract", "removal_condition",
    }
    if not expected_native_fields.issubset(set(deployment_native.get("required", []))):
        raise AssertionError("deployment native handoff schema is missing provider-owned adapter fields")
    properties = deployment_native["properties"]
    if properties["compatibility_adapter"].get("const") is not True:
        raise AssertionError("deployment native handoff must remain an explicit compatibility adapter")
    if properties["provider_contract"].get("const") != "contracts/native/native-artifact-v1.schema.json":
        raise AssertionError("deployment native handoff must reference the Senior 3 provider contract")
    expected_removal = "Remove after Senior 5 and external consumers stop registering this compatibility adapter for one release window."
    if properties["removal_condition"].get("const") != expected_removal:
        raise AssertionError("deployment native handoff removal condition differs from the provider lifecycle")

    bundle_targets = {record[1] for record in __import__("infrastructure.deployment.build_host_bundle", fromlist=["FILES"]).FILES}
    for target in (
        "/usr/local/lib/liqi-native/native/scripts/verify_artifact.py",
        "/usr/local/lib/liqi-native/native/scripts/verify_deployment_manifest.py",
        "/usr/local/share/liqi/contracts/runtime/runtime-config-v1.schema.json",
        "/usr/local/lib/liqi-database/contracts/database/migration-readiness-v1.schema.json",
        "/usr/local/lib/liqi-native/contracts/native/native-artifact-v1.schema.json",
        "/usr/local/lib/liqi-native/contracts/deployment/native-artifact-v1.schema.json",
        "/usr/local/libexec/liqi-configure-database-credentials",
        "/etc/systemd/system/liqi-database-credentials.service",
        "/usr/local/share/liqi/contracts/deployment/mix-deployment-v1.schema.json",
        "/usr/local/share/liqi/contracts/infrastructure/database-credentials-v1.schema.json",
    ):
        if target not in bundle_targets:
            raise AssertionError(f"signed host bundle is missing direct provider dependency: {target}")
    package_expectations = {
        "aarch64": {
            "path": "oracle-linux-9-aarch64-v1.json",
            "caddy_sha512": "bd06996e1612cf5e9770dc7134313067ef3fdfde43b1a2196004906006de779372dcf7a0e7c7fb0890c23791179f12be4625f1b6e666a2abf37e8aff4c3a1826",
            "otel_sha256": "c32b61c2ab0819312483425bf4a937c18bd1849704c89da909bc9bcd2cdf4c63",
            "cosign_sha256": "90e7ae0b5dfd60f20816b52c012addf7fc055ebcc7bea4ce81c428ca8518c302",
        },
        "x86_64": {
            "path": "oracle-linux-9-x86_64-v1.json",
            "caddy_sha512": "ee886eceda0ff9f30610d3be9b5b594026591e19add6b3961a341c72abe468e5eac9d7c2c2450bbb8420db1f827b954521f9336be4872f81090b8618adf8815a",
            "otel_sha256": "544e5a31d3171f74d698cb5696a6a3546eb9e86063bda9bfacd5325dbabda7ca",
            "cosign_sha256": "f7622ed3cf22e55e1ae6377c080979ff77a22da9981c11df222a2e444991e7cf",
        },
    }
    for architecture, expected in package_expectations.items():
        package_manifest = json.loads((INFRA / "packages" / expected["path"]).read_text(encoding="utf-8"))
        if package_manifest.get("architecture") != architecture:
            raise AssertionError(f"host package manifest architecture mismatch: {architecture}")
        packages = {item["name"]: item for item in package_manifest["packages"]}
        if packages.get("PostgreSQL", {}).get("version") != "17.10" or packages.get("PgBouncer", {}).get("version") != "1.25.2" or packages.get("pgBackRest", {}).get("version") != "2.58.0":
            raise AssertionError(f"database package pin changed for {architecture}")
        if packages.get("Caddy", {}).get("sha512") != expected["caddy_sha512"]:
            raise AssertionError(f"Caddy checksum pin changed for {architecture}")
        if packages.get("OpenTelemetry Collector", {}).get("sha256") != expected["otel_sha256"]:
            raise AssertionError(f"OpenTelemetry checksum pin changed for {architecture}")
        if packages.get("cosign", {}).get("sha256") != expected["cosign_sha256"]:
            raise AssertionError(f"cosign checksum pin changed for {architecture}")
    package_parser = (INFRA / "deployment/host_package_manifest.py").read_text(encoding="utf-8")
    installer = (INFRA / "bin/liqi-install-runtime-packages").read_text(encoding="utf-8")
    for token in ("host package manifest architecture mismatch", "x86_64-unknown-linux-gnu", "EL-9-x86_64", "linux_amd64"):
        if token not in package_parser:
            raise AssertionError(f"architecture-aware package manifest parser is missing {token}")
    for token in ("liqi-host-package-settings", "PACKAGE_INSTALL_NAMES", "package_manifest_sha256", "HOST_TARGET_TRIPLE"):
        if token not in installer:
            raise AssertionError(f"architecture-aware package installer is missing {token}")
    beam_unit = (systemd / "liqi-beam.service").read_text(encoding="utf-8")
    for token in (
        "LIQI_RUNTIME_CONFIG_PATH=/etc/liqi/runtime/current.json",
        "CREDENTIALS_DIRECTORY=/run/liqi/secrets/beam",
        "liqi-database-credentials.service",
    ):
        if token not in beam_unit:
            raise AssertionError(f"BEAM unit is not bound to Senior 1 runtime config semantics: {token}")
    if "127.0.0.1:4000" in caddy_live or "/socket/websocket" in caddy_live:
        raise AssertionError("Caddy contains stale draft runtime semantics")
    database_credential_unit = (systemd / "liqi-database-credentials.service").read_text(encoding="utf-8")
    database_credential_provider = (INFRA / "bin/liqi-configure-database-credentials").read_text(encoding="utf-8")
    for token in ("restore-transient --execute", "Before=pgbouncer.service liqi-beam.service"):
        if token not in database_credential_unit:
            raise AssertionError(f"database credential unit missing fail-closed lifecycle: {token}")
    for token in ("database-role-urls", "pgbouncer-userlist.txt", "SCRAM-SHA-256$", "approval-reference"):
        if token not in database_credential_provider:
            raise AssertionError(f"database credential provider missing contract token: {token}")


    commands = json.loads((INFRA / "deployment/commands-v1.json").read_text(encoding="utf-8"))
    command_ids = {item["id"] for item in commands["commands"]}
    for command_id in ("discover-e5-adoption", "validate-e5-state-adoption", "execute-e5-state-adoption", "establish-first-release-recovery", "pre-apply-readiness", "read-only-live-plan", "approved-oci-apply", "build-signed-linux-release"):
        if command_id not in command_ids:
            raise AssertionError(f"provider command registry is missing {command_id}")
    recovery_producer = (INFRA / "deployment/establish_first_release_recovery.py").read_text(encoding="utf-8")
    for token in ("traffic_is_off", "--wait-for-state", "FULL", "fallback instance has a public IP", "clean exact-SHA worktree is required", "oci_mutation_performed"):
        if token not in recovery_producer:
            raise AssertionError(f"first-release recovery producer is missing fail-closed token {token}")
    pre_apply = (INFRA / "validation/pre_apply_readiness.py").read_text(encoding="utf-8")
    for token in ("validate_linux_release_build_result.py", "state_mutation_performed", "oci_mutation_performed", "protected-environment"):
        if token not in pre_apply:
            raise AssertionError(f"pre-apply readiness aggregator is missing {token}")
    release_builder = (ROOT / "beam/scripts/build_linux_release.py").read_text(encoding="utf-8")
    for token in ("release build requires a clean exact-SHA worktree", "verify_deployment_manifest.py", "artifact and manifest signing key IDs must be distinct", "self-verification did not pass", "os.replace(staged_output, final_output)"):
        if token not in release_builder:
            raise AssertionError(f"canonical Linux release builder is missing {token}")
    release_workflow = (ROOT / ".github/workflows/v1-e5-artifact-release.yml").read_text(encoding="utf-8")
    for token in (
        'cd "$publication"',
        "find . -type f ! -name SHA256SUMS -print0",
        "LC_ALL=C sort -z",
        "sha256sum --check --strict SHA256SUMS",
    ):
        if token not in release_workflow:
            raise AssertionError(f"production publication checksum index is not portable or self-verified: {token}")
    if 'find "$publication" -type f -print0' in release_workflow:
        raise AssertionError("production publication checksum index contains absolute runner paths")

    runbook = (ROOT / "operations/runbooks/e5-temporary-adoption-v1.md").read_text(encoding="utf-8")
    for token in ("state adoption", "pre-apply-readiness", "does not create, update or delete OCI resources", "adopt-existing", "A1 remains the target profile"):
        if token not in runbook:
            raise AssertionError(f"E5 adoption runbook is missing {token}")

    otel = yaml.safe_load((INFRA / "otel/otelcol-v1.yaml").read_text(encoding="utf-8"))
    protocols = otel["receivers"]["otlp"]["protocols"]
    if protocols["grpc"]["endpoint"] != "127.0.0.1:4317" or protocols["http"]["endpoint"] != "127.0.0.1:4318":
        raise AssertionError("OTLP receivers must remain loopback-only")


def validate_bundle_build() -> None:
    import tarfile

    targets = {
        "aarch64-unknown-linux-gnu": "aarch64",
        "x86_64-unknown-linux-gnu": "x86_64",
    }
    with tempfile.TemporaryDirectory(prefix="liqi-host-bundle-test-") as directory:
        root = Path(directory)
        key = root / "key.pem"
        public = root / "key.pub.pem"
        run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)])
        run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)])
        sha = run(["git", "rev-parse", "HEAD"], capture=True)
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for target_triple, architecture in targets.items():
            bundle_id = f"source-validation-v1-{architecture.replace('_', '-')}"
            outputs = []
            for index in (1, 2):
                output = root / f"{architecture}-build-{index}"
                run([
                    sys.executable, str(INFRA / "deployment/build_host_bundle.py"),
                    "--git-sha", sha, "--bundle-id", bundle_id, "--key-id", "source-validation-v1",
                    "--target-triple", target_triple,
                    "--signing-key", str(key), "--output-dir", str(output), "--allow-dirty-source-test-only",
                ])
                outputs.append(output)
            names = [f"liqi-host-bundle-{bundle_id}.tar.gz", f"liqi-host-bundle-{bundle_id}.json", f"liqi-host-bundle-{bundle_id}.json.sig"]
            for name in names:
                first, second = outputs[0] / name, outputs[1] / name
                if hashlib.sha256(first.read_bytes()).digest() != hashlib.sha256(second.read_bytes()).digest():
                    raise AssertionError(f"host bundle output is not deterministic for {target_triple}: {name}")
            manifest = outputs[0] / names[1]
            document = json.loads(manifest.read_text(encoding="utf-8"))
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
            if errors:
                raise AssertionError(f"built host manifest does not satisfy contract: {errors[0].message}")
            if document["target_triple"] != target_triple:
                raise AssertionError(f"host bundle target mismatch: {document['target_triple']}")
            with tarfile.open(outputs[0] / names[0], "r:gz") as archive:
                member = archive.getmember("payload/usr/local/share/liqi/host-packages-v1.json")
                stream = archive.extractfile(member)
                package_manifest = json.loads(stream.read().decode("utf-8") if stream else "{}")
            if package_manifest.get("architecture") != architecture:
                raise AssertionError(f"host bundle package manifest mismatch for {target_triple}")
            run(["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(public), "-in", str(manifest), "-sigfile", str(outputs[0] / names[2])])


def main() -> int:
    try:
        run([sys.executable, str(INFRA / "validation/validate_v1_contracts.py")])
        validate_json_and_yaml()
        validate_shell_and_python()
        validate_static_policy()
        run([sys.executable, "-m", "unittest", "discover", "-s", str(INFRA / "validation"), "-p", "test_v1_*.py", "-v"])
        validate_opentofu_and_cloud_init()
        validate_bundle_build()
    except (AssertionError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"ERROR v1-source: {exc}", file=sys.stderr)
        return 1
    print("V1 Senior 4 source validation passed; no OCI or host mutation performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
