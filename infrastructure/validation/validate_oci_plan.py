#!/usr/bin/env python3
"""Validate a JSON-encoded OpenTofu plan for the default OCI V0 environment."""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover - explicit operator guidance
    raise SystemExit(
        "PyYAML is required; install with: "
        "python -m pip install -r infrastructure/validation/requirements.txt"
    ) from exc

EXPECTED_CREATE_COUNTS = {
    "oci_core_instance": 1,
    "oci_core_internet_gateway": 1,
    "oci_core_network_security_group": 1,
    "oci_core_network_security_group_security_rule": 9,
    "oci_core_route_table": 1,
    "oci_core_security_list": 1,
    "oci_core_service_gateway": 1,
    "oci_core_subnet": 1,
    "oci_core_vcn": 1,
    "oci_core_volume": 1,
    "oci_core_volume_attachment": 1,
    "oci_identity_compartment": 1,
    "oci_identity_dynamic_group": 1,
    "oci_identity_policy": 1,
    "oci_objectstorage_bucket": 1,
    "terraform_data": 4,
}
EXPECTED_OUTPUTS = {
    "oci_host_v0",
    "infrastructure_output_version",
    "replacement_impact",
}
FORBIDDEN_RENDERED_PATTERNS = {
    "private-key material": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY", re.I),
    "OCI API key path": re.compile(r"\.pem(?:[\"'\s]|$)|\.oci[/\\]config", re.I),
    "plaintext credential assignment": re.compile(
        r"(?mi)^\s*(?:password|private_key|secret_value|access_token|refresh_token)\s*[:=]"
    ),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def resolve_bash() -> str:
    candidates: list[str] = []
    configured = os.environ.get("LIQI_BASH")
    if configured:
        candidates.append(configured)
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    discovered = shutil.which("bash")
    if discovered:
        candidates.append(discovered)
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    fail("Bash is required to validate rendered host bootstrap scripts")


def find_module(plan: dict[str, object], address: str) -> dict[str, object]:
    root = plan["planned_values"]["root_module"]
    for module in root.get("child_modules", []):
        if module.get("address") == address:
            return module
    fail(f"missing planned module {address}")


def validate_actions(plan: dict[str, object]) -> Counter[str]:
    changes = plan.get("resource_changes", [])
    unexpected = [
        (change.get("address"), change.get("change", {}).get("actions"))
        for change in changes
        if change.get("change", {}).get("actions") != ["create"]
    ]
    if unexpected:
        fail(f"default clean-tenancy plan contains non-create actions: {unexpected}")

    counts = Counter(change["type"] for change in changes)
    if dict(counts) != EXPECTED_CREATE_COUNTS:
        fail(f"planned create resource counts changed: {dict(sorted(counts.items()))}")
    return counts


def validate_outputs(plan: dict[str, object]) -> None:
    outputs = plan.get("planned_values", {}).get("outputs", {})
    if set(outputs) != EXPECTED_OUTPUTS:
        fail(f"planned outputs changed: {sorted(outputs)}")
    for name, record in outputs.items():
        if record.get("sensitive") is True:
            fail(f"output {name} unexpectedly marked sensitive; host outputs must contain references only")

    contract = outputs["oci_host_v0"].get("value")
    if contract:
        if contract.get("schema_version") != "liqi.platform.oci-host/v0":
            fail("planned host output has unexpected schema_version")
        if contract.get("infrastructure_output_version") != "0.3.0":
            fail("planned host output has unexpected infrastructure_output_version")


def validate_instance_and_storage(module: dict[str, object]) -> str:
    resources = module.get("resources", [])
    instances = [resource for resource in resources if resource.get("type") == "oci_core_instance"]
    if len(instances) != 1:
        fail("plan must contain exactly one OCI compute instance")
    values = instances[0]["values"]
    if values.get("shape") != "VM.Standard.A1.Flex":
        fail(f"unexpected compute shape: {values.get('shape')}")
    shape_config = values.get("shape_config") or []
    if len(shape_config) != 1:
        fail("compute shape_config missing")
    if shape_config[0].get("ocpus") != 4 or shape_config[0].get("memory_in_gbs") != 24:
        fail(f"unexpected capacity: {shape_config[0]}")
    if values.get("preserve_boot_volume") is not False:
        fail("replaceable host boot volume must not be treated as durable data")

    source_details = values.get("source_details") or []
    if len(source_details) != 1 or int(source_details[0].get("boot_volume_size_in_gbs", 0)) != 50:
        fail("boot volume must be 50 GB")

    volumes = [resource for resource in resources if resource.get("type") == "oci_core_volume"]
    if len(volumes) != 1 or int(volumes[0]["values"].get("size_in_gbs", 0)) != 100:
        fail("separate durable data volume must be 100 GB")

    metadata = values.get("metadata") or {}
    if set(metadata) != {"ssh_authorized_keys", "user_data"}:
        fail(f"unexpected instance metadata keys: {sorted(metadata)}")
    user_data = metadata.get("user_data")
    if not isinstance(user_data, str):
        fail("rendered cloud-init user_data is missing")
    if len(user_data.encode("ascii")) > 16384:
        fail(f"encoded OCI user_data exceeds 16 KiB: {len(user_data.encode('ascii'))} bytes")
    compressed = base64.b64decode(user_data, validate=True)
    if not compressed.startswith(b"\x1f\x8b"):
        fail("OCI user_data must be gzip-compressed before Base64 encoding")
    return gzip.decompress(compressed).decode("utf-8")


def validate_ingress(module: dict[str, object]) -> None:
    rules = [
        resource
        for resource in module.get("resources", [])
        if resource.get("type") == "oci_core_network_security_group_security_rule"
    ]
    public_tcp: list[tuple[int, int]] = []
    for rule in rules:
        values = rule["values"]
        if (
            values.get("direction") != "INGRESS"
            or values.get("source") != "0.0.0.0/0"
            or values.get("protocol") != "6"
        ):
            continue
        for option in values.get("tcp_options") or []:
            for port_range in option.get("destination_port_range") or []:
                public_tcp.append((port_range["min"], port_range["max"]))
    if sorted(public_tcp) != [(80, 80), (443, 443)]:
        fail(f"public TCP ingress must be exactly 80/443, got {sorted(public_tcp)}")
    if any("ssh_ingress" in resource.get("address", "") for resource in rules):
        fail("default plan must not create public SSH ingress")


def validate_rendered_cloud_init(rendered: str) -> None:
    for label, pattern in FORBIDDEN_RENDERED_PATTERNS.items():
        if pattern.search(rendered):
            fail(f"rendered cloud-init contains forbidden {label}")

    document = yaml.safe_load(rendered)
    if not isinstance(document, dict):
        fail("rendered cloud-init is not a YAML mapping")
    if document.get("disable_root") is not True or document.get("ssh_pwauth") is not False:
        fail("rendered cloud-init does not disable root and password SSH")
    packages = document.get("packages") or []
    if "nginx" not in packages:
        fail("rendered cloud-init must install the infrastructure-owned NGINX edge")
    commands = [item for item in document.get("runcmd", []) if isinstance(item, list)]
    flattened_commands = "\n".join(" ".join(str(part) for part in item) for item in commands)
    for token in (
        "install -D -o root -g root -m 0644 /usr/local/share/liqi-bootstrap/nginx.conf /etc/nginx/nginx.conf",
        "setsebool -P httpd_can_network_connect 1",
        "nginx -t && systemctl enable --now nginx.service",
    ):
        if token not in flattened_commands:
            fail(f"rendered cloud-init missing edge activation command: {token}")

    write_files = document.get("write_files")
    if not isinstance(write_files, list) or not write_files:
        fail("rendered cloud-init write_files is missing")

    paths = {item.get("path") for item in write_files if isinstance(item, dict)}
    required_paths = {
        "/etc/ssh/sshd_config.d/60-liqi-hardening.conf",
        "/usr/local/sbin/liqi-create-runtime-identities",
        "/usr/local/sbin/liqi-prepare-data-volume",
        "/usr/local/sbin/liqi-write-host-readiness",
        "/etc/systemd/system/liqi-disable-swap.service",
        "/etc/systemd/system/liqi-data-volume.service",
        "/etc/systemd/system/liqi-host-readiness.service",
        "/etc/systemd/system/liqi-api.service",
        "/etc/systemd/system/liqi-realtime.service",
        "/etc/systemd/system/liqi-worker.service",
        "/usr/local/share/liqi-bootstrap/nginx.conf",
        "/usr/local/share/liqi-bootstrap/nginx-liqi-hardening.conf",
        "/etc/systemd/system/liqi-platform.slice",
        "/etc/systemd/system/liqi-platform-runtime.slice",
        "/etc/systemd/system/liqi-platform-database.slice",
        "/etc/systemd/system/liqi-platform-operations.slice",
        "/etc/systemd/system/liqi-platform-edge.slice",
        "/etc/systemd/system/liqi-api.service.d/10-capacity.conf",
        "/etc/systemd/system/liqi-realtime.service.d/10-capacity.conf",
        "/etc/systemd/system/liqi-worker.service.d/10-capacity.conf",
    }
    if not required_paths.issubset(paths):
        fail(f"rendered cloud-init missing files: {sorted(required_paths - paths)}")

    bash = resolve_bash()
    with tempfile.TemporaryDirectory(prefix="liqi-cloud-init-") as directory:
        temp_root = Path(directory)
        for index, item in enumerate(write_files):
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.startswith("#!/usr/bin/env bash"):
                continue
            script = temp_root / f"script-{index}.bash"
            script.write_text(content, encoding="utf-8", newline="\n")
            subprocess.run([bash, "-n", script.as_posix()], check=True)

    contents_by_path = {
        item.get("path"): item.get("content")
        for item in write_files
        if isinstance(item, dict) and isinstance(item.get("content"), str)
    }
    expected_provider_lines = {
        "/etc/systemd/system/liqi-api.service": {
            "User=liqi-api",
            "ExecStart=/opt/liqi/current/bin/liqi-api --config /etc/liqi/api.json",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "MemoryDenyWriteExecute=yes",
        },
        "/etc/systemd/system/liqi-realtime.service": {
            "User=liqi-realtime",
            "ExecStart=/opt/liqi/current/bin/liqi-realtime --config /etc/liqi/realtime.json",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "MemoryDenyWriteExecute=yes",
        },
        "/etc/systemd/system/liqi-worker.service": {
            "User=liqi-worker",
            "ExecStart=/opt/liqi/current/bin/liqi-worker --config /etc/liqi/worker.json",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "MemoryDenyWriteExecute=yes",
        },
        "/usr/local/share/liqi-bootstrap/nginx.conf": {
            "return 444;",
            "ssl_reject_handshake on;",
            "client_max_body_size 1m;",
            "include /etc/nginx/liqi-enabled/*.conf;",
        },
        "/usr/local/share/liqi-bootstrap/nginx-liqi-hardening.conf": {
            "Slice=liqi-platform-edge.slice",
            "CPUQuota=10%",
            "MemoryMax=256M",
            "MemorySwapMax=0",
        },
    }
    for provider_path, expected_lines in expected_provider_lines.items():
        content = contents_by_path.get(provider_path)
        if not isinstance(content, str):
            fail(f"rendered cloud-init missing provider output {provider_path}")
        actual_lines = {line.strip() for line in content.splitlines() if line.strip()}
        missing_lines = expected_lines - actual_lines
        if missing_lines:
            fail(f"provider output {provider_path} missing {sorted(missing_lines)}")

    expected_control_lines = {
        "/etc/systemd/system/liqi-platform.slice": {"CPUQuota=300%", "MemoryMax=20G", "MemorySwapMax=0"},
        "/etc/systemd/system/liqi-platform-runtime.slice": {"CPUQuota=145%", "MemoryMax=7G", "MemorySwapMax=0"},
        "/etc/systemd/system/liqi-platform-database.slice": {"CPUQuota=120%", "MemoryMax=7936M", "MemorySwapMax=0"},
        "/etc/systemd/system/liqi-platform-operations.slice": {"CPUQuota=25%", "MemoryMax=1G", "MemorySwapMax=0"},
        "/etc/systemd/system/liqi-platform-edge.slice": {"CPUQuota=10%", "MemoryMax=256M", "MemorySwapMax=0"},
        "/etc/systemd/system/liqi-api.service.d/10-capacity.conf": {"Slice=liqi-platform-runtime.slice", "CPUQuota=45%", "MemoryMax=2G", "MemorySwapMax=0", "Environment=LIQI_CONFIG_PATH=/etc/liqi/api.json"},
        "/etc/systemd/system/liqi-realtime.service.d/10-capacity.conf": {"Slice=liqi-platform-runtime.slice", "CPUQuota=65%", "MemoryMax=3G", "MemorySwapMax=0", "Environment=LIQI_CONFIG_PATH=/etc/liqi/realtime.json"},
        "/etc/systemd/system/liqi-worker.service.d/10-capacity.conf": {"Slice=liqi-platform-runtime.slice", "CPUQuota=35%", "MemoryMax=2G", "MemorySwapMax=0", "Environment=LIQI_CONFIG_PATH=/etc/liqi/worker.json"},
    }
    for control_path, expected_lines in expected_control_lines.items():
        content = contents_by_path.get(control_path)
        if not isinstance(content, str):
            fail(f"rendered cloud-init missing capacity control {control_path}")
        actual_lines = {line.strip() for line in content.splitlines() if line.strip()}
        missing_lines = expected_lines - actual_lines
        if missing_lines:
            fail(f"capacity control {control_path} missing {sorted(missing_lines)}")

    readiness_script = next(
        item["content"]
        for item in write_files
        if item.get("path") == "/usr/local/sbin/liqi-write-host-readiness"
    )
    required_readiness = (
        '"schema_version": "liqi.platform.host-readiness/v0"',
        '"status": "ready"',
        '"data_volume_mounted": "pass"',
        '"capacity_controls": "pass"',
        '"runtime_service_units": "pass"',
        '"edge_fail_closed": "pass"',
        'mv -f "$tmp_file" /run/liqi/host-ready.json',
    )
    for fragment in required_readiness:
        if fragment not in readiness_script:
            fail(f"readiness script missing {fragment}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_json", type=Path, help="Path produced by: tofu show -json PLAN")
    args = parser.parse_args()

    plan = json.loads(args.plan_json.read_text(encoding="utf-8"))
    counts = validate_actions(plan)
    validate_outputs(plan)
    module = find_module(plan, "module.secure_host")
    rendered_cloud_init = validate_instance_and_storage(module)
    validate_ingress(module)
    validate_rendered_cloud_init(rendered_cloud_init)

    print(f"validated read-only OCI V0 plan with {sum(counts.values())} create actions")
    print("validated one VM.Standard.A1.Flex host at 4 OCPU / 24 GB")
    print("validated public TCP ingress is 80/443 only and SSH is disabled")
    print("validated rendered cloud-init YAML and embedded Bash syntax")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
