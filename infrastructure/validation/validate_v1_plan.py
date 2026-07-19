#!/usr/bin/env python3
"""Validate an exact JSON OpenTofu plan for the V1 live environment."""
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
    "oci_core_nat_gateway": 1,
    "oci_core_service_gateway": 1,
    "oci_core_network_security_group": 2,
    "oci_core_network_security_group_security_rule": 16,
    "oci_core_route_table": 2,
    "oci_core_security_list": 1,
    "oci_core_subnet": 2,
    "oci_core_vcn": 1,
    "oci_core_volume": 1,
    "oci_core_volume_attachment": 1,
    "oci_identity_compartment": 1,
    "oci_identity_dynamic_group": 1,
    "oci_identity_policy": 1,
    "oci_kms_key": 1,
    "oci_kms_vault": 1,
    "oci_network_load_balancer_network_load_balancer": 1,
    "oci_network_load_balancer_backend_set": 2,
    "oci_network_load_balancer_backend": 2,
    "oci_network_load_balancer_listener": 2,
    "terraform_data": 6,
}
EXPECTED_OUTPUTS = {"oci_live_v1", "host_bootstrap_sha256", "replacement_impact"}
FORBIDDEN_TYPES = {"oci_vault_secret", "oci_database_db_system", "oci_containerengine_cluster", "oci_objectstorage_bucket"}
FORBIDDEN_TEXT = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
    re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@", re.I),
    re.compile(r'(?i)"(?:password|secret_value|access_token|refresh_token|private_key)"\s*:\s*"(?!<|\$\{)[^"]{8,}"'),
)
BASTION_SOURCES = {"10.42.20.100/32", "10.42.20.109/32"}


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


def resources_of(resources: list[dict[str, Any]], resource_type: str) -> list[dict[str, Any]]:
    return [item for item in resources if item.get("type") == resource_type]


def one(resources: list[dict[str, Any]], resource_type: str) -> dict[str, Any]:
    matches = resources_of(resources, resource_type)
    if len(matches) != 1:
        fail(f"expected one {resource_type}, got {len(matches)}")
    return matches[0]


def by_address(resources: list[dict[str, Any]], suffix: str) -> dict[str, Any]:
    matches = [item for item in resources if str(item.get("address", "")).endswith(suffix)]
    if len(matches) != 1:
        fail(f"expected one planned resource ending {suffix!r}, got {len(matches)}")
    return matches[0]


def expected_counts(allow_reserved_ip: bool) -> dict[str, int]:
    expected = dict(EXPECTED_COUNTS)
    if allow_reserved_ip:
        expected["oci_core_public_ip"] = 1
    return expected


def validate_actions(plan: dict[str, Any], allow_reserved_ip: bool, plan_mode: str = "initial-create") -> None:
    changes = [item for item in plan.get("resource_changes", []) if item.get("mode") == "managed"]
    counts = Counter(item.get("type") for item in changes)
    forbidden = sorted(set(counts) & FORBIDDEN_TYPES)
    if forbidden:
        fail(f"forbidden paid/secret resource types in plan: {forbidden}")
    if plan_mode == "initial-create":
        invalid = [(item.get("address"), item.get("change", {}).get("actions")) for item in changes if item.get("change", {}).get("actions") != ["create"]]
        if invalid:
            fail(f"initial V1 plan must contain create-only actions: {invalid}")
        expected = expected_counts(allow_reserved_ip)
        if dict(counts) != expected:
            fail(f"planned resource counts changed: actual={dict(sorted(counts.items()))} expected={dict(sorted(expected.items()))}")
        return
    if plan_mode != "adopt-existing":
        fail(f"unsupported plan mode: {plan_mode}")
    allowed_actions = {("no-op",), ("create",), ("update",)}
    invalid = []
    for item in changes:
        actions = tuple(item.get("change", {}).get("actions") or [])
        if actions not in allowed_actions:
            invalid.append((item.get("address"), list(actions)))
    if invalid:
        fail(f"adoption plan forbids delete/replacement or unknown actions: {invalid}")


def validate_planned_resource_counts(resources: list[dict[str, Any]], allow_reserved_ip: bool) -> None:
    counts = Counter(item.get("type") for item in resources if item.get("mode", "managed") == "managed")
    expected = expected_counts(allow_reserved_ip)
    if dict(counts) != expected:
        fail(f"planned resource graph changed: actual={dict(sorted(counts.items()))} expected={dict(sorted(expected.items()))}")


def validate_instance(resources: list[dict[str, Any]], capacity_profile: str, plan_mode: str = "initial-create") -> str:
    profiles = {"a1-target": {"shape": "VM.Standard.A1.Flex", "boot": 50}, "e5-temporary": {"shape": "VM.Standard.E5.Flex", "boot": 200}}
    expected = profiles[capacity_profile]
    values = one(resources, "oci_core_instance")["values"]
    if values.get("shape") != expected["shape"]:
        fail(f"compute shape must be {expected['shape']} for {capacity_profile}")
    shape = values.get("shape_config") or []
    if len(shape) != 1 or shape[0].get("ocpus") != 4 or shape[0].get("memory_in_gbs") != 24:
        fail(f"compute capacity must be 4 OCPU/24 GiB, got {shape}")
    source = values.get("source_details") or []
    if source and int(source[0].get("boot_volume_size_in_gbs", 0)) != expected["boot"]:
        fail(f"boot volume must be {expected['boot']} GiB for {capacity_profile}")
    rendered = ""
    metadata = values.get("metadata") or {}
    if plan_mode == "initial-create":
        if set(metadata) != {"user_data"}:
            fail(f"new instance metadata must contain only compressed user_data, got {sorted(metadata)}")
        encoded = metadata.get("user_data")
        if not isinstance(encoded, str) or len(encoded.encode("ascii")) > 16384:
            fail("encoded OCI user_data is absent or exceeds 16 KiB")
        compressed = base64.b64decode(encoded, validate=True)
        if not compressed.startswith(b"\x1f\x8b"):
            fail("OCI user_data must be gzip-compressed")
        rendered = gzip.decompress(compressed).decode("utf-8")
        for token in ("liqi-bootstrap-host", "liqi-install-host-bundle", "liqi-configure-bastion-ssh"):
            if token not in rendered:
                fail(f"baseline cloud-init is missing {token}")
        vnic = values.get("create_vnic_details") or []
        if len(vnic) != 1 or str(vnic[0].get("assign_public_ip")).lower() not in {"false", "0"}:
            fail("primary host must be created without a public IP")
    agent = values.get("agent_config") or []
    plugins = [item for block in agent for item in (block.get("plugins_config") or [])]
    if not any(item.get("name") == "Compute Instance Run Command" and item.get("desired_state") == "ENABLED" for item in plugins):
        fail("Compute Instance Run Command must remain enabled")
    return rendered


def validate_storage(resources: list[dict[str, Any]]) -> None:
    volume = one(resources, "oci_core_volume")["values"]
    if int(volume.get("size_in_gbs", 0)) != 130 or volume.get("vpus_per_gb") != 0:
        fail("preserved block volume must be 130 GiB at the declared lower-cost performance tier")
    vault = one(resources, "oci_kms_vault")["values"]
    key = one(resources, "oci_kms_key")["values"]
    if vault.get("vault_type") != "DEFAULT" or key.get("protection_mode") != "SOFTWARE":
        fail("V1 must use the free DEFAULT Vault/software-protected key profile")


def port_range(values: dict[str, Any], kind: str = "tcp_options") -> tuple[int, int] | None:
    options = values.get(kind) or []
    if len(options) != 1:
        return None
    ranges = options[0].get("destination_port_range") or []
    if len(ranges) != 1:
        return None
    return int(ranges[0]["min"]), int(ranges[0]["max"])


def validate_network(resources: list[dict[str, Any]]) -> None:
    igw = one(resources, "oci_core_internet_gateway")["values"]
    nat = one(resources, "oci_core_nat_gateway")["values"]
    service = one(resources, "oci_core_service_gateway")["values"]
    if igw.get("enabled") is not True:
        fail("separated public-edge Internet Gateway must be enabled")
    if nat.get("block_traffic") is True:
        fail("private-host NAT Gateway must not block traffic")
    if len(service.get("services") or []) != 1:
        fail("Service Gateway must target exactly one regional all-services entry")
    host_route = by_address(resources, "oci_core_route_table.edge")["values"].get("route_rules") or []
    edge_route = by_address(resources, "oci_core_route_table.public_edge")["values"].get("route_rules") or []
    host_destinations = {(r.get("destination"), r.get("destination_type")) for r in host_route}
    if ("0.0.0.0/0", "CIDR_BLOCK") not in host_destinations or not any(t == "SERVICE_CIDR_BLOCK" for _d, t in host_destinations):
        fail("host route table must contain NAT default and Service Gateway route")
    if {(r.get("destination"), r.get("destination_type")) for r in edge_route} != {("0.0.0.0/0", "CIDR_BLOCK")}:
        fail("public NLB edge route table must contain only an Internet Gateway default route")
    host_subnet = by_address(resources, "oci_core_subnet.edge")["values"]
    edge_subnet = by_address(resources, "oci_core_subnet.public_edge")["values"]
    if host_subnet.get("prohibit_public_ip_on_vnic") is not True:
        fail("host subnet must prohibit public IP assignment")
    if edge_subnet.get("prohibit_public_ip_on_vnic") is not False:
        fail("public NLB edge subnet must permit a public edge address")
    nlb = one(resources, "oci_network_load_balancer_network_load_balancer")["values"]
    if nlb.get("is_private") is not False or nlb.get("is_preserve_source_destination") is not False:
        fail("edge NLB must be public and use full NAT rather than exposing source addresses to the single backend")
    if len(nlb.get("network_security_group_ids") or []) != 1:
        fail("edge NLB must attach exactly one dedicated NSG")


def validate_ingress(resources: list[dict[str, Any]]) -> None:
    rules = resources_of(resources, "oci_core_network_security_group_security_rule")
    public_tcp: list[tuple[int, int]] = []
    bastion: set[str] = set()
    backend_ports: set[int] = set()
    for resource in rules:
        address = str(resource.get("address", ""))
        values = resource.get("values", {})
        if values.get("direction") != "INGRESS" or values.get("protocol") != "6":
            continue
        ports = port_range(values)
        if ports is None or ports[0] != ports[1]:
            continue
        port = ports[0]
        source = values.get("source")
        source_type = values.get("source_type")
        if source == "0.0.0.0/0":
            public_tcp.append((port, port))
            if "public_edge_ingress" not in address:
                fail(f"world TCP ingress is allowed outside the NLB edge: {address}")
        if port == 22:
            if source_type != "CIDR_BLOCK" or source not in BASTION_SOURCES:
                fail(f"SSH ingress must be one of the exact OCI Bastion /32 addresses: {address}")
            bastion.add(str(source))
        if "host_edge_ingress" in address:
            if source_type != "NETWORK_SECURITY_GROUP" or port not in {80, 443}:
                fail(f"host edge ingress must be NSG-to-NSG on 80/443: {address}")
            backend_ports.add(port)
    if sorted(public_tcp) != [(80, 80), (443, 443)]:
        fail(f"public TCP ingress must be exactly NLB 80/443, got {sorted(public_tcp)}")
    if bastion != BASTION_SOURCES:
        fail(f"SSH ingress must contain exactly the two accepted OCI Bastion sources, got {sorted(bastion)}")
    if backend_ports != {80, 443}:
        fail(f"host must accept exactly NLB backend ports 80/443, got {sorted(backend_ports)}")


def validate_nlb(resources: list[dict[str, Any]]) -> None:
    backend_ports = set()
    for item in resources_of(resources, "oci_network_load_balancer_backend_set"):
        values = item["values"]
        if values.get("policy") != "FIVE_TUPLE" or values.get("is_fail_open") is not False or values.get("is_preserve_source") is not False:
            fail("NLB backend sets must use FIVE_TUPLE, fail closed, and not preserve source")
        health = values.get("health_checker") or []
        if len(health) != 1 or health[0].get("protocol") != "TCP" or int(health[0].get("port", 0)) not in {80, 443}:
            fail("NLB backend health checks must be TCP on the matching 80/443 port")
    for item in resources_of(resources, "oci_network_load_balancer_backend"):
        values = item["values"]
        port = int(values.get("port", 0))
        if port not in {80, 443} or values.get("is_backup") is not False or values.get("is_drain") is not False or values.get("is_offline") is not False:
            fail("NLB backends must be active primary host ports 80/443")
        backend_ports.add(port)
    listeners = set()
    for item in resources_of(resources, "oci_network_load_balancer_listener"):
        values = item["values"]
        port = int(values.get("port", 0))
        if values.get("protocol") != "TCP" or port not in {80, 443} or int(values.get("tcp_idle_timeout", 0)) < 3600:
            fail("NLB listeners must be TCP 80/443 with WebSocket-safe idle timeout")
        listeners.add(port)
    if backend_ports != {80, 443} or listeners != {80, 443}:
        fail("NLB must contain exactly backend and listener ports 80/443")


def validate_management(resources: list[dict[str, Any]]) -> None:
    for resource in resources_of(resources, "oci_core_network_security_group_security_rule"):
        values = resource.get("values", {})
        if values.get("direction") == "EGRESS" and values.get("protocol") == "17" and values.get("destination") not in {"169.254.169.254/32", None}:
            fail("superseded external WireGuard UDP egress remains in the plan")


def validate_outputs(plan: dict[str, Any], mode: str, capacity_profile: str) -> None:
    outputs = plan.get("planned_values", {}).get("outputs", {})
    if set(outputs) != EXPECTED_OUTPUTS:
        fail(f"planned outputs changed: {sorted(outputs)}")
    contract = outputs["oci_live_v1"].get("value")
    if not isinstance(contract, dict):
        return
    if contract.get("schema_version") != "liqi.infrastructure.oci-live/v1" or contract.get("classification") != "production" or contract.get("infrastructure_output_version") != "1.3.0":
        fail("unexpected OCI output schema/classification/version")
    capacity = contract.get("capacity", {})
    expected = {
        "a1-target": {"shape": "VM.Standard.A1.Flex", "architecture": "aarch64", "target_triple": "aarch64-unknown-linux-gnu", "cost": "free-trial-only", "temporary": False},
        "e5-temporary": {"shape": "VM.Standard.E5.Flex", "architecture": "x86_64", "target_triple": "x86_64-unknown-linux-gnu", "cost": "paid-approved", "temporary": True},
    }[capacity_profile]
    for field in ("shape", "architecture", "target_triple", "temporary"):
        if capacity.get(field) != expected[field]:
            fail(f"capacity {field} does not match {capacity_profile}: {capacity.get(field)!r}")
    if capacity.get("profile") != capacity_profile or capacity.get("cost_classification") != expected["cost"]:
        fail("capacity profile or cost classification mismatch")
    if capacity_profile == "e5-temporary":
        if not capacity.get("expires_at") or capacity.get("migration_target_profile") != "a1-target":
            fail("temporary E5 output must carry expiry and A1 migration target")
    elif capacity.get("expires_at") is not None or capacity.get("migration_target_profile") is not None:
        fail("A1 target output must not carry temporary migration metadata")
    network = contract.get("network", {})
    required_network = {
        "host_public_ip_enabled": False,
        "outbound_internet_path": "nat-gateway",
        "oracle_services_path": "service-gateway",
        "ssh_default_enabled": True,
    }
    for name, expected_value in required_network.items():
        if network.get(name) != expected_value:
            fail(f"network output {name} does not match private-host topology")
    if set(network.get("ssh_source_cidrs") or []) != BASTION_SOURCES:
        fail("network output does not bind the two accepted OCI Bastion sources")
    management = network.get("management_access", {})
    if management.get("primary") != "oci-bastion-private-ssh" or management.get("secondary") != "oci-run-command" or management.get("public_ssh") is not False:
        fail("network output management access does not match accepted Bastion/Run Command boundary")
    host = contract.get("host", {})
    if host.get("public_ipv4") is not None or host.get("public_ip_mode") != "none":
        fail("primary host output must not expose a public IP")
    if mode == "approved-apply" and capacity_profile != "e5-temporary":
        fail("approved apply is enabled only for the temporary E5 bridge in this source revision")
    mutation = contract.get("mutation", {})
    if mutation.get("applied") is not (mode == "approved-apply"):
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
    parser.add_argument("--capacity-profile", choices=("a1-target", "e5-temporary"), default="a1-target")
    parser.add_argument("--plan-mode", choices=("initial-create", "adopt-existing"), default="initial-create")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        plan = load(args.plan_json)
        rendered = json.dumps(plan, sort_keys=True)
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(rendered):
                fail(f"plan contains forbidden credential material matching {pattern.pattern}")
        validate_actions(plan, args.allow_reserved_ip, args.plan_mode)
        resources = all_resources(plan)
        validate_planned_resource_counts(resources, args.allow_reserved_ip)
        validate_instance(resources, args.capacity_profile, args.plan_mode)
        validate_storage(resources)
        validate_network(resources)
        validate_ingress(resources)
        validate_nlb(resources)
        validate_management(resources)
        validate_outputs(plan, args.mode, args.capacity_profile)
    except (AssertionError, ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR v1-plan: {exc}", file=sys.stderr)
        return 1
    result = {
        "schema_version": "liqi.infrastructure.plan-validation-result/v1",
        "status": "passed",
        "mode": args.mode,
        "plan_mode": args.plan_mode,
        "capacity_profile": args.capacity_profile,
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
