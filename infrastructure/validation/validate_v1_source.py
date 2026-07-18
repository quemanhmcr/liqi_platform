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
    backend.write_text(backend.read_text(encoding="utf-8").replace('  backend "s3" {}\n\n', ''), encoding="utf-8", newline="\n")
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
        raise AssertionError("retained V0 runtime slice exceeds the shared parent ceiling")
    beam = (systemd / "liqi-beam.service").read_text(encoding="utf-8")
    if "MemoryDenyWriteExecute" in beam or "User=liqi-beam" not in beam or "NoNewPrivileges=yes" not in beam:
        raise AssertionError("BEAM unit hardening/JIT compatibility changed")

    module_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((INFRA / "opentofu/modules/oci-live-v1").glob("*.tf")))
    if "oci_vault_secret" in module_text or 'port = 22' in module_text or "enable_admin_ssh" in module_text:
        raise AssertionError("source introduces secret-in-state or SSH ingress")
    required_assignments = {
        "http": "80",
        "https": "443",
        "ocpus": "4",
        "memory_gib": "24",
        "boot_volume_gib": "50",
        "data_volume_gib": "130",
        "combined_storage_gib": "180",
    }
    for name, value in required_assignments.items():
        if not re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*{re.escape(value)}\s*$", module_text):
            raise AssertionError(f"missing capacity/network invariant: {name}={value}")

    backend = (ENV / "state-backend.hcl.example").read_text(encoding="utf-8")
    if "use_lockfile                = true" not in backend or "access_key" in backend or "secret_key" in backend:
        raise AssertionError("remote backend must use lockfile without inline credentials")
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
    package_manifest = json.loads((INFRA / "packages/oracle-linux-9-aarch64-v1.json").read_text(encoding="utf-8"))
    packages = {item["name"]: item for item in package_manifest["packages"]}
    if packages.get("PostgreSQL", {}).get("version") != "17.10":
        raise AssertionError("PostgreSQL reviewed minor pin changed")
    if "cosign" not in packages or not re.fullmatch(r"[0-9a-f]{64}", packages["cosign"].get("sha256", "")):
        raise AssertionError("cosign ARM64 verifier is not checksum pinned")
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

    otel = yaml.safe_load((INFRA / "otel/otelcol-v1.yaml").read_text(encoding="utf-8"))
    protocols = otel["receivers"]["otlp"]["protocols"]
    if protocols["grpc"]["endpoint"] != "127.0.0.1:4317" or protocols["http"]["endpoint"] != "127.0.0.1:4318":
        raise AssertionError("OTLP receivers must remain loopback-only")


def validate_bundle_build() -> None:
    with tempfile.TemporaryDirectory(prefix="liqi-host-bundle-test-") as directory:
        root = Path(directory)
        key = root / "key.pem"
        public = root / "key.pub.pem"
        run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)])
        run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)])
        sha = run(["git", "rev-parse", "HEAD"], capture=True)
        outputs = []
        for index in (1, 2):
            output = root / f"build-{index}"
            run([
                sys.executable, str(INFRA / "deployment/build_host_bundle.py"),
                "--git-sha", sha, "--bundle-id", "source-validation-v1", "--key-id", "source-validation-v1",
                "--signing-key", str(key), "--output-dir", str(output), "--allow-dirty-source-test-only",
            ])
            outputs.append(output)
        names = ["liqi-host-bundle-source-validation-v1.tar.gz", "liqi-host-bundle-source-validation-v1.json", "liqi-host-bundle-source-validation-v1.json.sig"]
        for name in names:
            first, second = outputs[0] / name, outputs[1] / name
            if hashlib.sha256(first.read_bytes()).digest() != hashlib.sha256(second.read_bytes()).digest():
                raise AssertionError(f"host bundle output is not deterministic: {name}")
        manifest = outputs[0] / names[1]
        document = json.loads(manifest.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker()).iter_errors(document))
        if errors:
            raise AssertionError(f"built host manifest does not satisfy contract: {errors[0].message}")
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
