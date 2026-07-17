#!/usr/bin/env python3
"""Stable Senior 1 validation entrypoint for OCI host source and contracts."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infrastructure"
MODULE = INFRA / "opentofu/modules/oci-secure-host-v0"
ENVIRONMENT = INFRA / "opentofu/environments/development"
COST_MANIFEST = INFRA / "opentofu/cost-classification.json"
CONTRACT_VALIDATOR = INFRA / "validation/validate_oci_host_contract.py"
CLOUD_INIT = INFRA / "cloud-init/host-bootstrap.yaml.tftpl"
CAPACITY_BUDGET = ROOT / "contracts/platform/infrastructure-capacity-budget-v0.json"

APPROVED_COST_CLASSES = {
    "always-free-safe",
    "free-trial-only",
    "paid",
    "unknown",
}
FORBIDDEN_PUBLIC_TCP_PORTS = {22, 4317, 4318, 5432, 6432, 8080, 8081, 8082}
REQUIRED_OUTPUTS = {
    "oci_host_v0",
    "infrastructure_output_version",
    "replacement_impact",
}
REQUIRED_DIRECTORY_PATHS = {
    "/opt/liqi/releases",
    "/etc/liqi",
    "/run/liqi/secrets",
    "/run/liqi/secrets/liqi-api",
    "/run/liqi/secrets/liqi-realtime",
    "/run/liqi/secrets/liqi-worker",
    "/var/tmp/liqi/releases",
    "/var/lib/liqi/postgresql/data",
    "/var/lib/liqi/postgresql/backup-staging",
    "/var/log/liqi",
    "/var/tmp/liqi",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run(command: list[str], cwd: Path | None = None) -> None:
    rendered = " ".join(command)
    print(f"+ {rendered}")
    subprocess.run(command, cwd=cwd or ROOT, check=True)


def terraform_blocks(text: str, keyword: str) -> Iterable[tuple[str, str, str]]:
    pattern = re.compile(rf'(?m)^\s*{re.escape(keyword)}\s+"([^"]+)"(?:\s+"([^"]+)")?\s*{{')
    for match in pattern.finditer(text):
        depth = 0
        in_string = False
        escaped = False
        end = None
        for index in range(match.end() - 1, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            fail(f"unterminated {keyword} block for {match.group(1)}")
        yield match.group(1), match.group(2) or "", text[match.start():end]


def validate_cost_manifest() -> None:
    manifest = json.loads(read(COST_MANIFEST))
    if manifest.get("schema_version") != "liqi.platform.infrastructure-cost/v0":
        fail("unexpected cost manifest schema_version")
    classified = manifest.get("resources")
    if not isinstance(classified, dict):
        fail("cost manifest resources must be an object")

    declared: set[str] = set()
    for tf_file in sorted(MODULE.glob("*.tf")):
        for resource_type, resource_name, _ in terraform_blocks(read(tf_file), "resource"):
            if resource_type.startswith("oci_"):
                declared.add(f"{resource_type}.{resource_name}")

    classified_addresses = set(classified)
    if declared != classified_addresses:
        fail(
            "cost classification mismatch; "
            f"missing={sorted(declared - classified_addresses)}, "
            f"stale={sorted(classified_addresses - declared)}"
        )

    for address, record in classified.items():
        classification = record.get("classification")
        if classification not in APPROVED_COST_CLASSES:
            fail(f"{address} has invalid cost classification {classification!r}")
        if not isinstance(record.get("default_enabled"), bool):
            fail(f"{address} must declare boolean default_enabled")
        if classification in {"paid", "unknown", "free-trial-only"} and record["default_enabled"]:
            fail(f"{address} is {classification} but default_enabled=true")
        if not str(record.get("rationale", "")).strip():
            fail(f"{address} is missing cost rationale")


def validate_network() -> None:
    network = read(MODULE / "network.tf")
    for resource_type, name, block in terraform_blocks(network, "resource"):
        if resource_type != "oci_core_network_security_group_security_rule":
            continue
        if 'direction                 = "INGRESS"' not in block:
            continue
        if 'source                    = "0.0.0.0/0"' not in block:
            continue

        tcp_ports = {
            int(value)
            for value in re.findall(r'(?m)^\s*(?:min|max)\s*=\s*([0-9]+)\s*$', block)
        }
        forbidden = tcp_ports & FORBIDDEN_PUBLIC_TCP_PORTS
        if forbidden:
            fail(f"NSG rule {name} publicly exposes forbidden TCP ports {sorted(forbidden)}")
        if name not in {"edge_ingress", "path_mtu_ingress"}:
            fail(f"unexpected world-source ingress rule: {name}")

    variables = read(MODULE / "variables.tf")
    if 'variable "enable_admin_ssh"' not in variables or 'default     = false' not in variables:
        fail("SSH must be disabled by default")
    if 'cidr != "0.0.0.0/0"' not in variables:
        fail("SSH variable validation must reject 0.0.0.0/0")


def validate_provider_capacity_budget() -> None:
    budget = json.loads(read(CAPACITY_BUDGET))
    if budget.get("schema_version") != "capacity-budget-v0":
        fail("infrastructure capacity budget has unexpected schema_version")
    if budget.get("provider") != "infrastructure" or budget.get("owner") != "Senior 1":
        fail("infrastructure capacity budget has incorrect provider ownership")
    components = budget.get("components")
    if not isinstance(components, list) or len(components) != 1:
        fail("infrastructure capacity budget must contain exactly the reverse proxy")
    component = components[0]
    expected = {
        "name": "reverse-proxy",
        "class": "edge",
        "default_enabled": True,
        "steady_state": {"ocpu": 0.05, "memory_mib": 96, "disk_gib": 1},
        "hard_limit": {"ocpu": 0.1, "memory_mib": 256, "disk_gib": 2},
        "postgres_connections": 0,
        "queue": {"bounded": True, "capacity": 1024, "overflow_behavior": "reject"},
        "retry": {
            "maximum_attempts": 0,
            "maximum_elapsed_seconds": 0,
            "backoff": "none",
            "jitter": False,
        },
        "failure_behavior": "fail-closed",
    }
    if component != expected:
        fail(f"infrastructure capacity budget changed: {component!r}")


def validate_capacity_and_outputs() -> None:
    module_variables = read(MODULE / "variables.tf")
    environment_main = read(ENVIRONMENT / "main.tf")
    combined = module_variables + "\n" + environment_main
    required_fragments = (
        'name                = "free-tier-a1-4x24"',
        'shape               = "VM.Standard.A1.Flex"',
        'architecture        = "aarch64"',
        "ocpus               = 4",
        "memory_gb           = 24",
        'cost_classification = "free-trial-only"',
        "boot_volume_gb      = 50",
        "data_volume_gb      = 100",
        'infrastructure_output_version = "0.3.0"',
        'bootstrap_version             = "0.3.0"',
    )
    for fragment in required_fragments:
        if fragment not in combined:
            fail(f"missing required capacity fragment: {fragment}")
    if "acknowledge_non_always_free_profile" not in read(MODULE / "guardrails.tf"):
        fail("non-Always-Free capacity profile lacks explicit cost acknowledgement guard")

    outputs_text = read(ENVIRONMENT / "outputs.tf")
    actual_outputs = {name for name, _, _ in terraform_blocks(outputs_text, "output")}
    if actual_outputs != REQUIRED_OUTPUTS:
        fail(f"required environment outputs mismatch: {sorted(actual_outputs)}")

    secret_field_pattern = re.compile(
        r'(?mi)^\s*(?:password|private_key|private-key|secret_value|secret-value|'
        r'access_token|refresh_token)\s*='
    )
    for output_file in (MODULE / "outputs.tf", ENVIRONMENT / "outputs.tf"):
        if secret_field_pattern.search(read(output_file)):
            fail(f"secret-bearing output field found in {output_file}")


def validate_cloud_init() -> None:
    text = read(CLOUD_INIT)
    required = (
        "disable_root: true",
        "ssh_pwauth: false",
        "PermitRootLogin no",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "AllowAgentForwarding no",
        "AllowTcpForwarding no",
        "swapoff -a",
        "SystemMaxUse=2G",
        "SystemKeepFree=10G",
        "RateLimitIntervalSec=30s",
        "RateLimitBurst=10000",
        "ForwardToSyslog=no",
        "liqi-disable-swap.service",
        "are_legacy_imds_endpoints_disabled",
        "liqi.platform.host-readiness/v0",
        "/run/liqi/host-ready.json",
        "mktemp /run/liqi/.host-ready.json",
        "mv -f \"$tmp_file\" /run/liqi/host-ready.json",
        "refusing to treat root filesystem as LIQI data volume",
        "wipefs --no-act",
        "nginx",
        "content: ${jsonencode(liqi_api_unit)}",
        "content: ${jsonencode(liqi_realtime_unit)}",
        "content: ${jsonencode(liqi_worker_unit)}",
        "content: ${jsonencode(nginx_config)}",
        "content: ${jsonencode(nginx_hardening)}",
        "/etc/systemd/system/liqi-api.service",
        "/etc/systemd/system/liqi-realtime.service",
        "/etc/systemd/system/liqi-worker.service",
        "/usr/local/share/liqi-bootstrap/nginx.conf",
        "/usr/local/share/liqi-bootstrap/nginx-liqi-hardening.conf",
        "nginx -t && systemctl enable --now nginx.service",
        "setsebool -P httpd_can_network_connect 1",
        "/etc/systemd/system/liqi-platform.slice",
        "CPUQuota=300%",
        "MemoryMax=20G",
        "MemorySwapMax=0",
        "/etc/systemd/system/liqi-platform-runtime.slice",
        "CPUQuota=145%",
        "MemoryMax=7G",
        "/etc/systemd/system/liqi-platform-database.slice",
        "CPUQuota=120%",
        "MemoryMax=7936M",
        "/etc/systemd/system/liqi-platform-operations.slice",
        "CPUQuota=25%",
        "/etc/systemd/system/liqi-platform-edge.slice",
        "CPUQuota=10%",
        "/etc/systemd/system/liqi-api.service.d/10-capacity.conf",
        "CPUQuota=45%",
        "Environment=LIQI_CONFIG_PATH=/etc/liqi/api.json",
        "/etc/systemd/system/liqi-realtime.service.d/10-capacity.conf",
        "CPUQuota=65%",
        "Environment=LIQI_CONFIG_PATH=/etc/liqi/realtime.json",
        "/etc/systemd/system/liqi-worker.service.d/10-capacity.conf",
        "CPUQuota=35%",
        "Environment=LIQI_CONFIG_PATH=/etc/liqi/worker.json",
        '"capacity_controls": "pass"',
        '"runtime_service_units": "pass"',
        '"edge_fail_closed": "pass"',
    )
    joined = text + "\n" + read(MODULE / "compute.tf")
    for fragment in required:
        if fragment not in joined:
            fail(f"cloud-init hardening/readiness fragment missing: {fragment}")

    if re.search(r'(?m)^\s*(?:permissions:|mode\s*=)\s*["\']?0?777', text):
        fail("cloud-init creates a world-writable managed directory")
    for directory in REQUIRED_DIRECTORY_PATHS:
        if directory not in text:
            fail(f"cloud-init does not materialize required directory {directory}")


def validate_provider_materialization() -> None:
    environment = read(ENVIRONMENT / "main.tf")
    compute = read(MODULE / "compute.tf")
    for fragment in (
        'file("${path.root}/../../../../services/systemd/liqi-api.service")',
        'file("${path.root}/../../../../services/systemd/liqi-realtime.service")',
        'file("${path.root}/../../../../services/systemd/liqi-worker.service")',
        'file("${path.root}/../../../edge/nginx.conf")',
        'file("${path.root}/../../../edge/nginx-liqi-hardening.conf")',
    ):
        if fragment not in environment:
            fail(f"environment does not consume provider output directly: {fragment}")
    if "user_data           = base64gzip(var.cloud_init_user_data)" not in compute:
        fail("OCI user_data must use gzip compression")
    if "length(base64gzip(var.cloud_init_user_data)) <= 16384" not in compute:
        fail("OCI user_data lacks the 16 KiB platform-limit precondition")

    service_expectations = {
        "liqi-api": ("liqi-api", "8080", "/etc/liqi/api.json"),
        "liqi-realtime": ("liqi-realtime", "8081", "/etc/liqi/realtime.json"),
        "liqi-worker": ("liqi-worker", "8082", "/etc/liqi/worker.json"),
    }
    for service, (user, _port, config) in service_expectations.items():
        unit_path = ROOT / "services" / "systemd" / f"{service}.service"
        if not unit_path.is_file():
            fail(f"runtime provider base unit missing: {unit_path.relative_to(ROOT)}")
        unit = read(unit_path)
        required = (
            f"User={user}",
            f"ExecStart=/opt/liqi/current/bin/{service} --config {config}",
            f"ExecStartPre=/usr/bin/test -r /run/liqi/secrets/{service}/database-password",
            "Restart=on-failure",
            "RestartSec=5s",
            "NoNewPrivileges=yes",
            "CapabilityBoundingSet=",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "MemoryDenyWriteExecute=yes",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        )
        for token in required:
            if token not in unit:
                fail(f"{unit_path.relative_to(ROOT)} missing hardening token {token}")
        if "User=root" in unit or "0.0.0.0" in unit:
            fail(f"{unit_path.relative_to(ROOT)} violates non-root/loopback ownership")

    nginx = read(INFRA / "edge" / "nginx.conf")
    for token in (
        "listen 80 default_server;",
        "return 444;",
        "listen 443 ssl default_server;",
        "ssl_reject_handshake on;",
        "server 127.0.0.1:8080",
        "server 127.0.0.1:8081",
        "client_max_body_size 1m;",
        "client_header_timeout 10s;",
        "client_body_timeout 10s;",
        "include /etc/nginx/liqi-enabled/*.conf;",
    ):
        if token not in nginx:
            fail(f"fail-closed NGINX source missing {token}")
    hardening = read(INFRA / "edge" / "nginx-liqi-hardening.conf")
    for token in (
        "Slice=liqi-platform-edge.slice",
        "CPUQuota=10%",
        "MemoryMax=256M",
        "MemorySwapMax=0",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "CapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_SETGID CAP_SETUID",
    ):
        if token not in hardening:
            fail(f"NGINX systemd hardening missing {token}")
    approved_site = read(INFRA / "edge" / "liqi-site-v0.conf.tftpl")
    for token in (
        "return 308 https://${server_name}$request_uri;",
        "ssl_protocols TLSv1.2 TLSv1.3;",
        "proxy_connect_timeout 3s;",
        "proxy_intercept_errors on;",
        "return 503 '{\"error\":\"service_unavailable\"}';",
    ):
        if token not in approved_site:
            fail(f"approved edge template missing {token}")


def validate_source_hygiene() -> None:
    forbidden_patterns = {
        "local OCI config": re.compile(r'(?:~|\$HOME|%USERPROFILE%)[/\\]\.oci|\.oci[/\\]config', re.I),
        "PEM path/material": re.compile(r'\.pem(?:["\'\s]|$)|BEGIN [A-Z ]*PRIVATE KEY', re.I),
        "OCI API private-key field": re.compile(r'(?m)^\s*(?:private_key|key_file|fingerprint)\s*=', re.I),
    }
    roots = [INFRA, ROOT / "security/iam", ROOT / "contracts/platform"]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".terraform" in path.parts or path.suffix == ".pyc":
                continue
            content = read(path)
            for label, pattern in forbidden_patterns.items():
                if pattern.search(content):
                    fail(f"{label} reference found in {path.relative_to(ROOT)}")


def validate_tofu() -> None:
    tofu = shutil.which("tofu")
    if tofu is None:
        fail("OpenTofu CLI is required for --with-tofu validation")
    run([tofu, "fmt", "-check", "-recursive", str(INFRA / "opentofu")])
    run([tofu, "init", "-backend=false", "-input=false"], cwd=ENVIRONMENT)
    run([tofu, "validate"], cwd=ENVIRONMENT)

    expression = (
        'jsonencode({encoded_size=length(base64gzip(local.cloud_init_user_data)),'
        'cloud_config_b64=base64encode(local.cloud_init_user_data)})'
    )
    command = [
        tofu,
        "console",
        "-var=tenancy_ocid=ocid1.tenancy.oc1..sourceonly",
        "-var=region=ap-singapore-2",
        "-var=availability_domain=source-only-ad",
        "-var=oracle_linux_image_ocid=ocid1.image.oc1.ap-singapore-2.sourceonly",
        "-var=admin_ssh_public_key=ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISourceOnlyFixture liqi-source-only",
    ]
    completed = subprocess.run(
        command,
        cwd=ENVIRONMENT,
        input=expression + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = json.loads(json.loads(completed.stdout.strip()))
    encoded_size = int(rendered["encoded_size"])
    if encoded_size > 16384:
        fail(f"rendered OCI user_data exceeds 16 KiB: {encoded_size} bytes")
    cloud_config = base64.b64decode(rendered["cloud_config_b64"], validate=True).decode("utf-8")
    document = yaml.safe_load(cloud_config)
    if not isinstance(document, dict):
        fail("rendered cloud-init is not a YAML object")
    write_files = document.get("write_files")
    if not isinstance(write_files, list):
        fail("rendered cloud-init has no write_files list")
    by_path = {
        item.get("path"): item.get("content")
        for item in write_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    expected_provider_bytes = {
        "/etc/systemd/system/liqi-api.service": read(ROOT / "services/systemd/liqi-api.service"),
        "/etc/systemd/system/liqi-realtime.service": read(ROOT / "services/systemd/liqi-realtime.service"),
        "/etc/systemd/system/liqi-worker.service": read(ROOT / "services/systemd/liqi-worker.service"),
        "/usr/local/share/liqi-bootstrap/nginx.conf": read(INFRA / "edge/nginx.conf"),
        "/usr/local/share/liqi-bootstrap/nginx-liqi-hardening.conf": read(
            INFRA / "edge/nginx-liqi-hardening.conf"
        ),
    }
    for destination, expected in expected_provider_bytes.items():
        if by_path.get(destination) != expected:
            fail(f"rendered cloud-init does not preserve exact provider bytes for {destination}")
    commands = "\n".join(
        " ".join(str(part) for part in item)
        for item in document.get("runcmd", [])
        if isinstance(item, list)
    )
    for service in ("liqi-api", "liqi-realtime", "liqi-worker"):
        if f"enable --now {service}.service" in commands:
            fail(f"bootstrap must not enable runtime service before activation: {service}")
    print(f"rendered gzip OCI user_data: {encoded_size} bytes (limit 16384)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-tofu",
        action="store_true",
        help="also run tofu fmt/init/validate; requires provider download or cache",
    )
    args = parser.parse_args()

    run([sys.executable, str(CONTRACT_VALIDATOR)])
    validate_cost_manifest()
    validate_provider_capacity_budget()
    validate_network()
    validate_capacity_and_outputs()
    validate_cloud_init()
    validate_provider_materialization()
    validate_source_hygiene()
    if args.with_tofu:
        validate_tofu()

    print("validated OCI secure-host V0 source, cost, security, output, and bootstrap invariants")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
