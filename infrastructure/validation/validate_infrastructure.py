#!/usr/bin/env python3
"""Stable Senior 1 validation entrypoint for OCI host source and contracts."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infrastructure"
MODULE = INFRA / "opentofu/modules/oci-secure-host-v0"
ENVIRONMENT = INFRA / "opentofu/environments/development"
COST_MANIFEST = INFRA / "opentofu/cost-classification.json"
CONTRACT_VALIDATOR = INFRA / "validation/validate_oci_host_contract.py"
CLOUD_INIT = INFRA / "cloud-init/host-bootstrap.yaml.tftpl"

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
        "SystemKeepFree=7G",
        "liqi-disable-swap.service",
        "are_legacy_imds_endpoints_disabled",
        "liqi.platform.host-readiness/v0",
        "/run/liqi/host-ready.json",
        "mktemp /run/liqi/.host-ready.json",
        "mv -f \"$tmp_file\" /run/liqi/host-ready.json",
        "refusing to treat root filesystem as LIQI data volume",
        "wipefs --no-act",
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
    validate_network()
    validate_capacity_and_outputs()
    validate_cloud_init()
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
