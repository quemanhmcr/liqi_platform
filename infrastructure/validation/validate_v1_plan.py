#!/usr/bin/env python3
"""Validate an exact JSON OpenTofu plan for the initial V1 live environment."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_COUNTS = {
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
    "oci_kms_key": 1,
    "oci_kms_vault": 1,
    "oci_objectstorage_bucket": 1,
    "terraform_data": 5,
}
EXPECTED_OUTPUTS = {"oci_live_v1", "host_bootstrap_sha256", "replacement_impact"}
FORBIDDEN_TYPES = {"oci_vault_secret", "oci_database_db_system", "oci_containerengine_cluster"}
FORBIDDEN_TEXT = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
    re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@", re.I),
    re.compile(r'(?i)"(?:password|secret_value|access_token|refresh_token|private_key)"\s*:\s*"(?!<|\$\{)[^"]{8,}"'),
)


def fail(message: str) -> None:
    raise AssertionError(message)


def load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        fail("plan JSON root must be an object")
    return document


def all_modules(module: dict[str, Any]):
    yield module
    for child in module.get("child_modules", []):
        yield from all_modules(child)


def all_resources(plan: dict[str, Any]) -> list[dict[str, Any]]:
    root = plan.get("planned_values", {}).get("root_module", {})
    return [resource for module in all_modules(root) for resource in module.get("resources", [])]


def one(resources: list[dict[str, Any]], resource_type: str) -> dict[str, Any]:
    matches = [item for item in resources if item.get("type") == resource_type]
    if len(matches) != 1:
        fail(f"expected one {resource_type}, got {len(matches)}")
    return matches[0]


def validate_actions(plan: dict[str, Any], allow_reserved_ip: bool) -> None:
    changes = [item for item in plan.get("resource_changes", []) if item.get("mode") == "managed"]
    invalid = [
        (item.get("address"), item.get("change", {}).get("actions"))
        for item in changes
        if item.get("change", {}).get("actions") != ["create"]
    ]
    if invalid:
        fail(f"initial V1 plan must contain create-only actions: {invalid}")
    counts = Counter(item.get("type") for item in changes)
    expected = dict(EXPECTED_COUNTS)
    if allow_reserved_ip:
        expected["oci_core_public_ip"] = 1
    if dict(counts) != expected:
        fail(f"planned resource counts changed: actual={dict(sorted(counts.items()))} expected={dict(sorted(expected.items()))}")
    forbidden = sorted(set(counts) & FORBIDDEN_TYPES)
    if forbidden:
        fail(f"forbidden paid/secret resource types in plan: {forbidden}")


def validate_instance(resources: list[dict[str, Any]]) -> str:
    values = one(resources, "oci_core_instance")["values"]
    if values.get("shape") != "VM.Standard.A1.Flex":
        fail("compute shape must be VM.Standard.A1.Flex")
    shape = values.get("shape_config") or []
    if len(shape) != 1 or shape[0].get("ocpus") != 4 or shape[0].get("memory_in_gbs") != 24:
        fail(f"compute capacity must be 4 OCPU/24 GiB, got {shape}")
    source = values.get("source_details") or []
    if len(source) != 1 or int(source[0].get("boot_volume_size_in_gbs", 0)) != 50:
        fail("boot volume must be 50 GiB")
    metadata = values.get("metadata") or {}
    if set(metadata) != {"user_data"}:
        fail(f"instance metadata must contain only compressed user_data, got {sorted(metadata)}")
    encoded = metadata.get("user_data")
    if not isinstance(encoded, str) or len(encoded.encode("ascii")) > 16384:
        fail("encoded OCI user_data is absent or exceeds 16 KiB")
    compressed = base64.b64decode(encoded, validate=True)
    if not compressed.startswith(b"\x1f\x8b"):
        fail("OCI user_data must be gzip-compressed")
    rendered = gzip.decompress(compressed).decode("utf-8")
    if "liqi-bootstrap-host" not in rendered or "liqi-install-host-bundle" not in rendered:
        fail("baseline cloud-init is missing guarded bootstrap/host-bundle providers")
    if "sshd.service sshd.socket" not in rendered:
        fail("baseline cloud-init does not mask SSH")
    return rendered


def validate_storage(resources: list[dict[str, Any]]) -> None:
    volume = one(resources, "oci_core_volume")["values"]
    if int(volume.get("size_in_gbs", 0)) != 130 or volume.get("vpus_per_gb") != 0:
        fail("preserved block volume must be 130 GiB at the declared lower-cost performance tier")
    bucket = one(resources, "oci_objectstorage_bucket")["values"]
    if bucket.get("access_type") != "NoPublicAccess" or bucket.get("versioning") != "Enabled":
        fail("backup bucket must be private and versioned")
    vault = one(resources, "oci_kms_vault")["values"]
    key = one(resources, "oci_kms_key")["values"]
    if vault.get("vault_type") != "DEFAULT" or key.get("protection_mode") != "SOFTWARE":
        fail("V1 must use the free DEFAULT Vault/software-protected key profile")


def validate_ingress(resources: list[dict[str, Any]]) -> None:
    public_tcp: list[tuple[int, int]] = []
    for resource in resources:
        if resource.get("type") != "oci_core_network_security_group_security_rule":
            continue
        values = resource.get("values", {})
        if values.get("direction") != "INGRESS" or values.get("source") != "0.0.0.0/0" or values.get("protocol") != "6":
            continue
        for option in values.get("tcp_options") or []:
            for port_range in option.get("destination_port_range") or []:
                public_tcp.append((int(port_range["min"]), int(port_range["max"])))
    if sorted(public_tcp) != [(80, 80), (443, 443)]:
        fail(f"public TCP ingress must be exactly 80/443, got {sorted(public_tcp)}")


def validate_outputs(plan: dict[str, Any], mode: str) -> None:
    outputs = plan.get("planned_values", {}).get("outputs", {})
    if set(outputs) != EXPECTED_OUTPUTS:
        fail(f"planned outputs changed: {sorted(outputs)}")
    contract = outputs["oci_live_v1"].get("value")
    if not isinstance(contract, dict):
        return  # OCIDs can keep the composite value unknown in some provider versions.
    if contract.get("schema_version") != "liqi.infrastructure.oci-live/v1":
        fail("unexpected OCI output schema version")
    mutation = contract.get("mutation", {})
    expected_applied = mode == "approved-apply"
    if mutation.get("applied") is not expected_applied:
        fail(f"plan mutation marker does not match mode={mode}")
    if mode == "approved-apply" and not mutation.get("approval_reference"):
        fail("approved apply plan must carry the explicit approval reference")
    if mode == "plan" and mutation.get("approval_reference") is not None:
        fail("read-only plan must not claim an approval reference")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_json", type=Path)
    parser.add_argument("--mode", choices=("plan", "approved-apply"), default="plan")
    parser.add_argument("--allow-reserved-ip", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = load(args.plan_json)
        rendered = json.dumps(plan, sort_keys=True)
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(rendered):
                fail(f"plan contains forbidden credential material matching {pattern.pattern}")
        validate_actions(plan, args.allow_reserved_ip)
        resources = all_resources(plan)
        validate_instance(resources)
        validate_storage(resources)
        validate_ingress(resources)
        validate_outputs(plan, args.mode)
    except (AssertionError, ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR v1-plan: {exc}", file=sys.stderr)
        return 1

    result = {
        "schema_version": "liqi.infrastructure.plan-validation-result/v1",
        "status": "passed",
        "mode": args.mode,
        "plan_sha256": hashlib.sha256(args.plan_json.read_bytes()).hexdigest(),
        "public_tcp_ports": [80, 443],
        "oci_mutation_performed": False,
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
