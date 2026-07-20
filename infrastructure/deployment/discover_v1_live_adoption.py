#!/usr/bin/env python3
"""Discover compatible OCI resources for a no-mutation V1 state adoption."""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/infrastructure/adoption-manifest-v1.schema.json"

# Static source graph for the E5 retained-compute/public-NLB lane. The optional
# reserved public IP and the stopped recovery fallback are deliberately outside
# OpenTofu mutation scope.
ADDRESSES: dict[str, tuple[str, str]] = {
    "compartment": ("module.v1_live.oci_identity_compartment.environment", "oci_identity_compartment"),
    "vcn": ("module.v1_live.oci_core_vcn.main", "oci_core_vcn"),
    "internet_gateway": ("module.v1_live.oci_core_internet_gateway.main", "oci_core_internet_gateway"),
    "nat_gateway": ("module.v1_live.oci_core_nat_gateway.outbound", "oci_core_nat_gateway"),
    "service_gateway": ("module.v1_live.oci_core_service_gateway.oracle_services", "oci_core_service_gateway"),
    "legacy_route_table": ("module.v1_live.oci_core_route_table.edge", "oci_core_route_table"),
    "route_table": ("module.v1_live.oci_core_route_table.private_host", "oci_core_route_table"),
    "public_edge_route_table": ("module.v1_live.oci_core_route_table.public_edge", "oci_core_route_table"),
    "security_list": ("module.v1_live.oci_core_security_list.empty", "oci_core_security_list"),
    "legacy_subnet": ("module.v1_live.oci_core_subnet.edge", "oci_core_subnet"),
    "subnet": ("module.v1_live.oci_core_subnet.private_host", "oci_core_subnet"),
    "public_edge_subnet": ("module.v1_live.oci_core_subnet.public_edge", "oci_core_subnet"),
    "nsg": ("module.v1_live.oci_core_network_security_group.host", "oci_core_network_security_group"),
    "nlb_nsg": ("module.v1_live.oci_core_network_security_group.public_edge", "oci_core_network_security_group"),
    "bastion_100": ('module.v1_live.oci_core_network_security_group_security_rule.bastion_ssh_ingress["10.42.20.100/32"]', "oci_core_network_security_group_security_rule"),
    "bastion_109": ('module.v1_live.oci_core_network_security_group_security_rule.bastion_ssh_ingress["10.42.20.109/32"]', "oci_core_network_security_group_security_rule"),
    "host_http": ('module.v1_live.oci_core_network_security_group_security_rule.host_edge_ingress["http"]', "oci_core_network_security_group_security_rule"),
    "host_https": ('module.v1_live.oci_core_network_security_group_security_rule.host_edge_ingress["https"]', "oci_core_network_security_group_security_rule"),
    "host_pmtu": ("module.v1_live.oci_core_network_security_group_security_rule.host_path_mtu_ingress", "oci_core_network_security_group_security_rule"),
    "egress_80": ('module.v1_live.oci_core_network_security_group_security_rule.web_egress["80"]', "oci_core_network_security_group_security_rule"),
    "egress_443": ('module.v1_live.oci_core_network_security_group_security_rule.web_egress["443"]', "oci_core_network_security_group_security_rule"),
    "dns_udp": ("module.v1_live.oci_core_network_security_group_security_rule.dns_udp_egress", "oci_core_network_security_group_security_rule"),
    "dns_tcp": ("module.v1_live.oci_core_network_security_group_security_rule.dns_tcp_egress", "oci_core_network_security_group_security_rule"),
    "ntp": ("module.v1_live.oci_core_network_security_group_security_rule.ntp_egress", "oci_core_network_security_group_security_rule"),
    "oracle_services_egress": ("module.v1_live.oci_core_network_security_group_security_rule.oracle_services_egress", "oci_core_network_security_group_security_rule"),
    "edge_http": ('module.v1_live.oci_core_network_security_group_security_rule.public_edge_ingress["http"]', "oci_core_network_security_group_security_rule"),
    "edge_https": ('module.v1_live.oci_core_network_security_group_security_rule.public_edge_ingress["https"]', "oci_core_network_security_group_security_rule"),
    "edge_pmtu": ("module.v1_live.oci_core_network_security_group_security_rule.public_edge_path_mtu_ingress", "oci_core_network_security_group_security_rule"),
    "edge_egress_http": ('module.v1_live.oci_core_network_security_group_security_rule.public_edge_egress["http"]', "oci_core_network_security_group_security_rule"),
    "edge_egress_https": ('module.v1_live.oci_core_network_security_group_security_rule.public_edge_egress["https"]', "oci_core_network_security_group_security_rule"),
    "legacy_instance": ("module.v1_live.oci_core_instance.host", "oci_core_instance"),
    "data_volume": ("module.v1_live.oci_core_volume.data", "oci_core_volume"),
    "data_attachment": ("module.v1_live.oci_core_volume_attachment.data", "oci_core_volume_attachment"),
    "vault": ("module.v1_live.oci_kms_vault.main", "oci_kms_vault"),
    "key": ("module.v1_live.oci_kms_key.main", "oci_kms_key"),
    "dynamic_group": ("module.v1_live.oci_identity_dynamic_group.host", "oci_identity_dynamic_group"),
    "policy": ("module.v1_live.oci_identity_policy.host", "oci_identity_policy"),
    "nlb": ("module.v1_live.oci_network_load_balancer_network_load_balancer.edge", "oci_network_load_balancer_network_load_balancer"),
    "backend_set_http": ('module.v1_live.oci_network_load_balancer_backend_set.edge["http"]', "oci_network_load_balancer_backend_set"),
    "backend_set_https": ('module.v1_live.oci_network_load_balancer_backend_set.edge["https"]', "oci_network_load_balancer_backend_set"),
    "backend_http": ('module.v1_live.oci_network_load_balancer_backend.host["http"]', "oci_network_load_balancer_backend"),
    "backend_https": ('module.v1_live.oci_network_load_balancer_backend.host["https"]', "oci_network_load_balancer_backend"),
    "listener_http": ('module.v1_live.oci_network_load_balancer_listener.edge["http"]', "oci_network_load_balancer_listener"),
    "listener_https": ('module.v1_live.oci_network_load_balancer_listener.edge["https"]', "oci_network_load_balancer_listener"),
}

RESOURCE_NAME_KEYS = {
    "compartment", "vcn", "internet_gateway", "nat_gateway", "service_gateway",
    "legacy_route_table", "route_table", "public_edge_route_table", "security_list", "legacy_subnet", "subnet",
    "public_edge_subnet", "nsg", "nlb_nsg", "network_load_balancer", "legacy_instance", "legacy_vnic",
    "legacy_fallback_instance",
    "data_volume", "data_attachment", "vault", "key", "reserved_public_ip",
    "dynamic_group", "policy",
}
BASTION_SOURCES = {"10.42.20.100/32": "bastion_100", "10.42.20.109/32": "bastion_109"}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_config(profile: str) -> tuple[str, str]:
    path = Path(os.environ.get("OCI_CLI_CONFIG_FILE", Path.home() / ".oci" / "config"))
    parser = configparser.RawConfigParser()
    parser.read(path, encoding="utf-8")
    section = "DEFAULT" if profile == "DEFAULT" else profile
    if section != "DEFAULT" and not parser.has_section(section):
        raise SystemExit(f"OCI profile is missing: {profile}")
    tenancy = parser.defaults().get("tenancy") if section == "DEFAULT" else parser.get(section, "tenancy", fallback="")
    region = parser.defaults().get("region") if section == "DEFAULT" else parser.get(section, "region", fallback="")
    if not tenancy or not region:
        raise SystemExit("OCI profile must contain tenancy and region")
    return tenancy, region


def oci(profile: str, region: str, *args: str) -> Any:
    command = ["oci", *args, "--profile", profile, "--region", region, "--output", "json"]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"OCI command failed: {' '.join(command[:4])}")
    payload = completed.stdout.strip()
    if not payload:
        if "list" in args:
            return []
        raise RuntimeError("OCI get command returned empty JSON")
    try:
        data = json.loads(payload).get("data")
        # Most OCI CLI list commands return data as an array. NLB list uses a
        # paginated collection object with an items array.
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        return data
    except json.JSONDecodeError as exc:
        raise RuntimeError("OCI command returned invalid JSON") from exc


def one(items: list[dict[str, Any]], field: str, value: str) -> dict[str, Any] | None:
    matches = [item for item in items if item.get(field) == value and item.get("lifecycle-state") not in {"DELETED", "TERMINATED"}]
    if len(matches) > 1:
        raise RuntimeError(f"multiple active resources match {field}={value!r}")
    return matches[0] if matches else None


def add_import(records: list[dict[str, str]], key: str, item: dict[str, Any] | None, display_name: str, import_id: str | None = None) -> None:
    if not item:
        return
    address, resource_type = ADDRESSES[key]
    records.append({"address": address, "id": import_id or item["id"], "resource_type": resource_type, "display_name": display_name})


def nsg_rule_import_id(nsg_id: str, rule_id: str) -> str:
    return f"networkSecurityGroups/{nsg_id}/securityRules/{rule_id}"


def tcp_destination(rule: dict[str, Any]) -> tuple[int, int] | None:
    options = rule.get("tcp-options") or {}
    value = options.get("destination-port-range") or {}
    if "min" not in value or "max" not in value:
        return None
    return int(value["min"]), int(value["max"])


def udp_destination(rule: dict[str, Any]) -> tuple[int, int] | None:
    options = rule.get("udp-options") or {}
    value = options.get("destination-port-range") or {}
    if "min" not in value or "max" not in value:
        return None
    return int(value["min"]), int(value["max"])


def icmp_type_code(rule: dict[str, Any]) -> tuple[int, int | None] | None:
    options = rule.get("icmp-options") or {}
    if "type" not in options:
        return None
    code = options.get("code")
    return int(options["type"]), int(code) if code is not None else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--compartment-name", default="liqi-live")
    parser.add_argument("--resource-names", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True, stdout=subprocess.PIPE, check=True).stdout.strip():
        raise SystemExit("clean worktree is required for an exact-SHA adoption manifest")
    names = json.loads(args.resource_names.read_text(encoding="utf-8"))
    if set(names) != RESOURCE_NAME_KEYS:
        raise SystemExit("resource-names JSON keys do not match the adoption contract")
    tenancy, region = load_config(args.profile)
    if region != "ap-singapore-2":
        raise SystemExit("V1 adoption is restricted to ap-singapore-2")

    compartments = oci(args.profile, region, "iam", "compartment", "list", "--compartment-id", tenancy, "--all")
    compartment = one(compartments, "name", args.compartment_name)
    if not compartment:
        raise SystemExit("target compartment does not exist")
    compartment_id = compartment["id"]
    imports: list[dict[str, str]] = []
    blockers: list[str] = []
    unmanaged: list[dict[str, str]] = []
    add_import(imports, "compartment", compartment, args.compartment_name)

    vcn = one(oci(args.profile, region, "network", "vcn", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["vcn"])
    add_import(imports, "vcn", vcn, names["vcn"])
    if not vcn:
        blockers.append("adopted VCN is missing")
        vcn_id = None
    else:
        vcn_id = vcn["id"]
        if (vcn.get("cidr-blocks") or []) != ["10.42.0.0/16"]:
            blockers.append("adopted VCN must be exactly 10.42.0.0/16")

    host_nsg: dict[str, Any] | None = None
    host_subnet: dict[str, Any] | None = None
    if vcn_id:
        igw = one(oci(args.profile, region, "network", "internet-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["internet_gateway"])
        nat = one(oci(args.profile, region, "network", "nat-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["nat_gateway"])
        service = one(oci(args.profile, region, "network", "service-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["service_gateway"])
        routes = oci(args.profile, region, "network", "route-table", "list", "--compartment-id", compartment_id, "--all")
        legacy_route = one(routes, "display-name", names["legacy_route_table"])
        host_route = one(routes, "display-name", names["route_table"])
        public_edge_route = one(routes, "display-name", names["public_edge_route_table"])
        security_lists = oci(args.profile, region, "network", "security-list", "list", "--compartment-id", compartment_id, "--vcn-id", vcn_id, "--all")
        security_list = one(security_lists, "display-name", names["security_list"])
        subnets = oci(args.profile, region, "network", "subnet", "list", "--compartment-id", compartment_id, "--all")
        legacy_subnet = one(subnets, "display-name", names["legacy_subnet"])
        host_subnet = one(subnets, "display-name", names["subnet"])
        public_edge_subnet = one(subnets, "display-name", names["public_edge_subnet"])
        nsgs = oci(args.profile, region, "network", "nsg", "list", "--compartment-id", compartment_id, "--all")
        host_nsg = one(nsgs, "display-name", names["nsg"])
        nlb_nsg = one(nsgs, "display-name", names["nlb_nsg"])

        for key, item in (("internet_gateway", igw), ("nat_gateway", nat), ("service_gateway", service), ("legacy_route_table", legacy_route), ("route_table", host_route), ("public_edge_route_table", public_edge_route), ("security_list", security_list), ("legacy_subnet", legacy_subnet), ("subnet", host_subnet), ("public_edge_subnet", public_edge_subnet), ("nsg", host_nsg), ("nlb_nsg", nlb_nsg)):
            if item and item.get("vcn-id") != vcn_id:
                blockers.append(f"{key} does not belong to the adopted VCN")
            else:
                add_import(imports, key, item, names[key])

        if not igw:
            blockers.append("internet gateway required for the separated NLB edge is missing")
        # A disabled IGW is an allowed in-place update because the primary host
        # route remains NAT-only; it is not an adoption blocker.
        if not nat or nat.get("lifecycle-state") != "AVAILABLE" or nat.get("block-traffic"):
            blockers.append("adopted NAT gateway must be AVAILABLE and unblocked")
        if not service or service.get("lifecycle-state") != "AVAILABLE" or service.get("block-traffic"):
            blockers.append("adopted Service Gateway must be AVAILABLE and unblocked")
        if host_route and nat and service:
            rules = host_route.get("route-rules") or []
            default_ok = any(r.get("destination") == "0.0.0.0/0" and r.get("network-entity-id") == nat["id"] for r in rules)
            service_ok = any(r.get("destination-type") == "SERVICE_CIDR_BLOCK" and r.get("network-entity-id") == service["id"] for r in rules)
            if not default_ok or not service_ok:
                blockers.append("host route table must retain NAT default and Service Gateway Oracle Services routes")
        if legacy_route and nat and service:
            rules = legacy_route.get("route-rules") or []
            default_ok = any(r.get("destination") == "0.0.0.0/0" and r.get("network-entity-id") == nat["id"] for r in rules)
            service_ok = any(r.get("destination-type") == "SERVICE_CIDR_BLOCK" and r.get("network-entity-id") == service["id"] for r in rules)
            if not default_ok or not service_ok:
                blockers.append("legacy route table must remain NAT-only plus Oracle Services while retained")
        if legacy_subnet and (
            legacy_subnet.get("cidr-block") != "10.42.10.0/24"
            or legacy_subnet.get("prohibit-public-ip-on-vnic") is not False
        ):
            blockers.append("retained legacy subnet must remain the existing public-IP-capable 10.42.10.0/24 resource")
        if not legacy_subnet or not legacy_route:
            blockers.append("retained legacy subnet and route table must exist before additive migration")
        if host_subnet and (
            host_subnet.get("cidr-block") != "10.42.20.0/24"
            or host_subnet.get("prohibit-public-ip-on-vnic") is not True
        ):
            blockers.append("adopted production host subnet must be private 10.42.20.0/24 and prohibit public IPs")
        if not host_subnet or not host_route:
            blockers.append("existing private host subnet and route table are required for additive migration")
        if public_edge_subnet and public_edge_subnet.get("cidr-block") != "10.42.30.0/24":
            blockers.append("public NLB edge subnet must be exactly 10.42.30.0/24")
        if security_list:
            ingress_rules = security_list.get("ingress-security-rules") or []
            egress_rules = security_list.get("egress-security-rules") or []
            egress_ok = (
                len(egress_rules) == 1
                and egress_rules[0].get("destination") == "0.0.0.0/0"
                and egress_rules[0].get("destination-type") == "CIDR_BLOCK"
                and egress_rules[0].get("protocol") == "all"
                and not egress_rules[0].get("is-stateless", False)
            )
            if ingress_rules or not egress_ok:
                blockers.append("adopted subnet Security List must have zero ingress and one stateful all-protocol egress rule")

        if host_nsg:
            rules = oci(args.profile, region, "network", "nsg", "rules", "list", "--nsg-id", host_nsg["id"], "--all")
            recognized_rule_ids: set[str] = set()

            def add_rule(key: str, candidates: list[dict[str, Any]], label: str, required: bool = False) -> None:
                if len(candidates) > 1:
                    blockers.append(f"workload NSG contains multiple rules matching {label}")
                    return
                if not candidates:
                    if required:
                        blockers.append(f"workload NSG is missing required {label}")
                    return
                rule = candidates[0]
                recognized_rule_ids.add(rule["id"])
                add_import(imports, key, rule, label, nsg_rule_import_id(host_nsg["id"], rule["id"]))

            for source, key in BASTION_SOURCES.items():
                matches = [r for r in rules if r.get("direction") == "INGRESS" and r.get("protocol") == "6" and r.get("source") == source and tcp_destination(r) == (22, 22)]
                add_rule(key, matches, f"Bastion SSH rule for {source}", required=True)
            if nlb_nsg:
                for key, port in (("host_http", 80), ("host_https", 443)):
                    add_rule(key, [r for r in rules if r.get("direction") == "INGRESS" and r.get("protocol") == "6" and r.get("source-type") == "NETWORK_SECURITY_GROUP" and r.get("source") == nlb_nsg["id"] and tcp_destination(r) == (port, port)], f"NLB-to-host TCP/{port}")
            add_rule("host_pmtu", [r for r in rules if r.get("direction") == "INGRESS" and r.get("protocol") == "1" and r.get("source") == "10.42.0.0/16" and icmp_type_code(r) == (3, 4)], "VCN path-MTU ICMP")
            for key, port in (("egress_80", 80), ("egress_443", 443)):
                add_rule(key, [r for r in rules if r.get("direction") == "EGRESS" and r.get("protocol") == "6" and r.get("destination") == "0.0.0.0/0" and tcp_destination(r) == (port, port)], f"web egress TCP/{port}")
            add_rule("dns_udp", [r for r in rules if r.get("direction") == "EGRESS" and r.get("protocol") == "17" and r.get("destination") == "169.254.169.254/32" and udp_destination(r) == (53, 53)], "OCI resolver UDP/53")
            add_rule("dns_tcp", [r for r in rules if r.get("direction") == "EGRESS" and r.get("protocol") == "6" and r.get("destination") == "169.254.169.254/32" and tcp_destination(r) == (53, 53)], "OCI resolver TCP/53")
            add_rule("ntp", [r for r in rules if r.get("direction") == "EGRESS" and r.get("protocol") == "17" and r.get("destination") == "169.254.169.254/32" and udp_destination(r) == (123, 123)], "OCI NTP UDP/123")
            add_rule("oracle_services_egress", [r for r in rules if r.get("direction") == "EGRESS" and r.get("protocol") == "6" and r.get("destination-type") == "SERVICE_CIDR_BLOCK" and tcp_destination(r) == (443, 443)], "Oracle Services TCP/443")
            if {r.get("id") for r in rules} - recognized_rule_ids:
                blockers.append("workload NSG contains rules outside the exact retained-compute source graph")
        if nlb_nsg:
            rules = oci(args.profile, region, "network", "nsg", "rules", "list", "--nsg-id", nlb_nsg["id"], "--all")
            recognized_rule_ids: set[str] = set()

            def add_edge_rule(key: str, candidates: list[dict[str, Any]], label: str) -> None:
                if len(candidates) > 1:
                    blockers.append(f"public-edge NSG contains multiple rules matching {label}")
                    return
                if not candidates:
                    return
                rule = candidates[0]
                recognized_rule_ids.add(rule["id"])
                add_import(imports, key, rule, label, nsg_rule_import_id(nlb_nsg["id"], rule["id"]))

            for key, port in (("edge_http", 80), ("edge_https", 443)):
                add_edge_rule(key, [r for r in rules if r.get("direction") == "INGRESS" and r.get("protocol") == "6" and r.get("source") == "0.0.0.0/0" and tcp_destination(r) == (port, port)], f"public TCP/{port}")
            add_edge_rule("edge_pmtu", [r for r in rules if r.get("direction") == "INGRESS" and r.get("protocol") == "1" and r.get("source") == "0.0.0.0/0" and icmp_type_code(r) == (3, 4)], "public path-MTU ICMP")
            if host_nsg:
                for key, port in (("edge_egress_http", 80), ("edge_egress_https", 443)):
                    add_edge_rule(key, [r for r in rules if r.get("direction") == "EGRESS" and r.get("protocol") == "6" and r.get("destination-type") == "NETWORK_SECURITY_GROUP" and r.get("destination") == host_nsg["id"] and tcp_destination(r) == (port, port)], f"edge-to-host TCP/{port}")
            if {r.get("id") for r in rules} - recognized_rule_ids:
                blockers.append("public-edge NSG contains rules outside the exact source graph")
        for item in nsgs:
            if item.get("display-name") not in {names["nsg"], names["nlb_nsg"]}:
                unmanaged.append({"kind": "network-security-group", "display_name": item.get("display-name") or "unnamed", "reason": "Additional NSG remains outside the reviewed V1 graph."})

    instances = oci(args.profile, region, "compute", "instance", "list", "--compartment-id", compartment_id, "--all")
    legacy_instance = one(instances, "display-name", names["legacy_instance"])
    retained_fallback = one(instances, "display-name", names["legacy_fallback_instance"])
    add_import(imports, "legacy_instance", legacy_instance, names["legacy_instance"])
    if not legacy_instance:
        blockers.append("retained private primary must exist before first-release reconciliation")
    if not retained_fallback:
        blockers.append("retained stopped first-release fallback must exist")

    for role, candidate in (
        ("retained primary", legacy_instance),
        ("retained fallback", retained_fallback),
    ):
        if not candidate:
            continue
        shape = candidate.get("shape-config") or {}
        if candidate.get("shape") != "VM.Standard.E5.Flex" or shape.get("ocpus") != 4 or shape.get("memory-in-gbs") != 24:
            blockers.append(f"{role} must be VM.Standard.E5.Flex at exactly 4 OCPU/24 GiB")
        attachments = oci(args.profile, region, "compute", "vnic-attachment", "list", "--compartment-id", compartment_id, "--instance-id", candidate["id"], "--all")
        active_attachments = [item for item in attachments if item.get("lifecycle-state") == "ATTACHED"]
        if len(active_attachments) != 1:
            blockers.append(f"{role} must have exactly one attached primary VNIC")
        else:
            vnic = oci(args.profile, region, "network", "vnic", "get", "--vnic-id", active_attachments[0]["vnic-id"])
            if vnic.get("public-ip"):
                blockers.append(f"{role} must not have a public IP")
            if host_nsg and host_nsg["id"] not in (vnic.get("nsg-ids") or []):
                blockers.append(f"{role} VNIC is not attached to the adopted workload NSG")
        boot_attachments = oci(args.profile, region, "compute", "boot-volume-attachment", "list", "--compartment-id", compartment_id, "--instance-id", candidate["id"], "--availability-domain", candidate["availability-domain"])
        active_boots = [item for item in boot_attachments if item.get("lifecycle-state") == "ATTACHED"]
        if len(active_boots) != 1:
            blockers.append(f"{role} must have exactly one active boot volume attachment")
        else:
            boot = oci(args.profile, region, "bv", "boot-volume", "get", "--boot-volume-id", active_boots[0]["boot-volume-id"])
            if int(boot.get("size-in-gbs", 0)) != 200:
                blockers.append(f"{role} boot volume must be exactly 200 GiB")
    if legacy_instance and legacy_instance.get("lifecycle-state") != "RUNNING":
        blockers.append("retained primary must be RUNNING")
    if retained_fallback and retained_fallback.get("lifecycle-state") != "STOPPED":
        blockers.append("retained first-release fallback must remain STOPPED")

    reviewed_names = {names["legacy_instance"], names["legacy_fallback_instance"]}
    for item in instances:
        if item.get("display-name") not in reviewed_names and item.get("lifecycle-state") != "TERMINATED":
            unmanaged.append({"kind": "compute-instance", "display_name": item.get("display-name") or "unnamed", "reason": "Additional instance remains outside the reviewed retained-compute source graph."})
            blockers.append("unexpected non-terminated compute instance exists outside the retained primary/fallback pair")
        elif item.get("display-name") == names["legacy_fallback_instance"] and item.get("lifecycle-state") != "TERMINATED":
            unmanaged.append({"kind": "compute-instance", "display_name": item.get("display-name") or "unnamed", "reason": "Retained stopped first-release fallback is the recovery authority and remains outside OpenTofu mutation scope."})

    volumes = oci(args.profile, region, "bv", "volume", "list", "--compartment-id", compartment_id, "--all")
    volume = one(volumes, "display-name", names["data_volume"])
    add_import(imports, "data_volume", volume, names["data_volume"])
    if volume and (int(volume.get("size-in-gbs", 0)) != 130 or int(volume.get("vpus-per-gb", 0)) != 0):
        blockers.append("adopted data volume must be 130 GiB at 0 VPUs/GB")
    if volume and legacy_instance:
        attachments = oci(args.profile, region, "compute", "volume-attachment", "list", "--compartment-id", compartment_id, "--all")
        attachment = next((item for item in attachments if item.get("volume-id") == volume["id"] and item.get("instance-id") == legacy_instance["id"] and item.get("lifecycle-state") != "DETACHED"), None)
        add_import(imports, "data_attachment", attachment, names["data_attachment"])

    vault = one(oci(args.profile, region, "kms", "management", "vault", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["vault"])
    add_import(imports, "vault", vault, names["vault"])
    if vault:
        key = one(oci(args.profile, region, "kms", "management", "key", "list", "--compartment-id", compartment_id, "--endpoint", vault["management-endpoint"], "--all"), "display-name", names["key"])
        add_import(imports, "key", key, names["key"], f"managementEndpoint/{vault['management-endpoint']}/keys/{key['id']}" if key else None)

    dynamic_groups = oci(args.profile, region, "iam", "dynamic-group", "list", "--compartment-id", tenancy, "--all")
    policies = oci(args.profile, region, "iam", "policy", "list", "--compartment-id", tenancy, "--all")
    add_import(imports, "dynamic_group", one(dynamic_groups, "name", names["dynamic_group"]), names["dynamic_group"])
    add_import(imports, "policy", one(policies, "name", names["policy"]), names["policy"])

    nlbs = oci(args.profile, region, "nlb", "network-load-balancer", "list", "--compartment-id", compartment_id, "--all")
    nlb = one(nlbs, "display-name", names["network_load_balancer"])
    add_import(imports, "nlb", nlb, names["network_load_balancer"])
    if nlb:
        backend_sets = oci(args.profile, region, "nlb", "backend-set", "list", "--network-load-balancer-id", nlb["id"], "--all")
        listeners = oci(args.profile, region, "nlb", "listener", "list", "--network-load-balancer-id", nlb["id"], "--all")
        for lane, port in (("http", 80), ("https", 443)):
            backend_set_name = f"liqi-{lane}-backends"
            backend_set = one(backend_sets, "name", backend_set_name)
            backend_set_id = f"networkLoadBalancers/{nlb['id']}/backendSets/{backend_set_name}"
            add_import(imports, f"backend_set_{lane}", backend_set, backend_set_name, backend_set_id)
            if backend_set:
                health = backend_set.get("health-checker") or {}
                if backend_set.get("policy") != "FIVE_TUPLE" or backend_set.get("is-fail-open") is not False or health.get("protocol") != "TCP" or int(health.get("port", 0)) != port:
                    blockers.append(f"existing {lane} backend set does not match the fail-closed source contract")
                backends = oci(args.profile, region, "nlb", "backend", "list", "--network-load-balancer-id", nlb["id"], "--backend-set-name", backend_set_name, "--all")
                matches = [item for item in backends if legacy_instance and item.get("target-id") == legacy_instance["id"] and int(item.get("port", 0)) == port]
                if len(matches) > 1:
                    blockers.append(f"multiple retained-primary backends exist for {lane}")
                elif matches:
                    backend = matches[0]
                    backend_id = f"{backend_set_id}/backends/{backend['name']}"
                    add_import(imports, f"backend_{lane}", backend, backend["name"], backend_id)
                    if backend.get("is-offline") is not True:
                        blockers.append(f"existing {lane} backend must remain offline before public cutover")
            listener_name = f"liqi-{lane}-listener"
            listener = one(listeners, "name", listener_name)
            listener_id = f"networkLoadBalancers/{nlb['id']}/listeners/{listener_name}"
            add_import(imports, f"listener_{lane}", listener, listener_name, listener_id)
            if listener and (listener.get("protocol") != "TCP" or int(listener.get("port", 0)) != port or int(listener.get("tcp-idle-timeout", 0)) != 1800):
                blockers.append(f"existing {lane} listener does not match TCP/{port} with 1800-second idle timeout")

    imported_addresses = {item["address"] for item in imports}
    missing = sorted(address for address, _kind in ADDRESSES.values() if address not in imported_addresses)
    document = {
        "schema_version": "liqi.infrastructure.adoption-manifest/v1",
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "capacity_profile": "e5-temporary",
        "region": region,
        "compartment_name": args.compartment_name,
        "discovered_at": utc(),
        "status": "blocked" if blockers else "passed",
        "imports": sorted(imports, key=lambda item: item["address"]),
        "missing_addresses": missing,
        "unmanaged_resources": sorted(unmanaged, key=lambda item: (item["kind"], item["display_name"])),
        "blockers": blockers,
        "oci_mutation_performed": False,
    }
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise SystemExit(f"generated adoption manifest is invalid: {errors[0].message}")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.new.{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)
    print(json.dumps({"status": document["status"], "imports": len(imports), "missing": len(missing), "unmanaged": len(unmanaged), "blockers": len(blockers), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, sort_keys=True))
    return 0 if not blockers else 3


if __name__ == "__main__":
    raise SystemExit(main())
