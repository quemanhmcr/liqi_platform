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

# Static source graph for the E5 private-host/public-NLB lane. The optional
# reserved public IP is deliberately excluded unless a later reviewed source
# makes it mandatory.
ADDRESSES: dict[str, tuple[str, str]] = {
    "compartment": ("module.v1_live.oci_identity_compartment.environment", "oci_identity_compartment"),
    "vcn": ("module.v1_live.oci_core_vcn.main", "oci_core_vcn"),
    "internet_gateway": ("module.v1_live.oci_core_internet_gateway.main", "oci_core_internet_gateway"),
    "nat_gateway": ("module.v1_live.oci_core_nat_gateway.outbound", "oci_core_nat_gateway"),
    "service_gateway": ("module.v1_live.oci_core_service_gateway.oracle_services", "oci_core_service_gateway"),
    "route_table": ("module.v1_live.oci_core_route_table.edge", "oci_core_route_table"),
    "public_edge_route_table": ("module.v1_live.oci_core_route_table.public_edge", "oci_core_route_table"),
    "security_list": ("module.v1_live.oci_core_security_list.empty", "oci_core_security_list"),
    "subnet": ("module.v1_live.oci_core_subnet.edge", "oci_core_subnet"),
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
    "instance": ("module.v1_live.oci_core_instance.host", "oci_core_instance"),
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
    "route_table", "public_edge_route_table", "security_list", "subnet",
    "public_edge_subnet", "nsg", "nlb_nsg", "network_load_balancer", "instance",
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
    instance: dict[str, Any] | None = None
    if vcn_id:
        igw = one(oci(args.profile, region, "network", "internet-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["internet_gateway"])
        nat = one(oci(args.profile, region, "network", "nat-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["nat_gateway"])
        service = one(oci(args.profile, region, "network", "service-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["service_gateway"])
        routes = oci(args.profile, region, "network", "route-table", "list", "--compartment-id", compartment_id, "--all")
        host_route = one(routes, "display-name", names["route_table"])
        public_edge_route = one(routes, "display-name", names["public_edge_route_table"])
        security_lists = oci(args.profile, region, "network", "security-list", "list", "--compartment-id", compartment_id, "--vcn-id", vcn_id, "--all")
        security_list = one(security_lists, "display-name", names["security_list"])
        subnets = oci(args.profile, region, "network", "subnet", "list", "--compartment-id", compartment_id, "--all")
        host_subnet = one(subnets, "display-name", names["subnet"])
        public_edge_subnet = one(subnets, "display-name", names["public_edge_subnet"])
        nsgs = oci(args.profile, region, "network", "nsg", "list", "--compartment-id", compartment_id, "--all")
        host_nsg = one(nsgs, "display-name", names["nsg"])
        nlb_nsg = one(nsgs, "display-name", names["nlb_nsg"])

        for key, item in (("internet_gateway", igw), ("nat_gateway", nat), ("service_gateway", service), ("route_table", host_route), ("public_edge_route_table", public_edge_route), ("security_list", security_list), ("subnet", host_subnet), ("public_edge_subnet", public_edge_subnet), ("nsg", host_nsg), ("nlb_nsg", nlb_nsg)):
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
        if host_subnet and host_subnet.get("cidr-block") != "10.42.10.0/24":
            blockers.append("adopted host subnet must be exactly 10.42.10.0/24")
        if public_edge_subnet and public_edge_subnet.get("cidr-block") != "10.42.30.0/24":
            blockers.append("public NLB edge subnet must be exactly 10.42.30.0/24")
        if security_list and ((security_list.get("ingress-security-rules") or []) or (security_list.get("egress-security-rules") or [])):
            blockers.append("adopted subnet Security List must remain empty")

        if host_nsg:
            rules = oci(args.profile, region, "network", "nsg", "rules", "list", "--nsg-id", host_nsg["id"], "--all")
            for source, key in BASTION_SOURCES.items():
                matches = [r for r in rules if r.get("direction") == "INGRESS" and r.get("protocol") == "6" and r.get("source") == source and tcp_destination(r) == (22, 22)]
                if len(matches) != 1:
                    blockers.append(f"workload NSG must contain exactly one Bastion SSH rule for {source}")
                else:
                    add_import(imports, key, matches[0], source, nsg_rule_import_id(host_nsg["id"], matches[0]["id"]))
            for rule in rules:
                if rule.get("direction") == "INGRESS" and rule.get("protocol") == "6" and tcp_destination(rule) == (22, 22) and rule.get("source") not in BASTION_SOURCES:
                    blockers.append("workload NSG contains SSH ingress outside the two accepted OCI Bastion /32 addresses")
        for item in nsgs:
            if item.get("display-name") not in {names["nsg"], names["nlb_nsg"]}:
                unmanaged.append({"kind": "network-security-group", "display_name": item.get("display-name") or "unnamed", "reason": "Additional NSG remains outside the reviewed V1 graph."})

    instances = oci(args.profile, region, "compute", "instance", "list", "--compartment-id", compartment_id, "--all")
    instance = one(instances, "display-name", names["instance"])
    add_import(imports, "instance", instance, names["instance"])
    if instance:
        shape = instance.get("shape-config") or {}
        if instance.get("shape") != "VM.Standard.E5.Flex" or shape.get("ocpus") != 4 or shape.get("memory-in-gbs") != 24:
            blockers.append("temporary E5 instance must be VM.Standard.E5.Flex at exactly 4 OCPU/24 GiB")
        attachments = oci(args.profile, region, "compute", "vnic-attachment", "list", "--compartment-id", compartment_id, "--instance-id", instance["id"], "--all")
        if len(attachments) != 1:
            blockers.append("temporary E5 instance must have exactly one primary VNIC")
        else:
            vnic = oci(args.profile, region, "network", "vnic", "get", "--vnic-id", attachments[0]["vnic-id"])
            if vnic.get("public-ip"):
                blockers.append("temporary E5 primary must not have a public IP")
            if host_nsg and host_nsg["id"] not in (vnic.get("nsg-ids") or []):
                blockers.append("temporary E5 primary VNIC is not attached to the adopted workload NSG")
        boot_attachments = oci(args.profile, region, "compute", "boot-volume-attachment", "list", "--compartment-id", compartment_id, "--instance-id", instance["id"], "--availability-domain", instance["availability-domain"])
        if len(boot_attachments) != 1:
            blockers.append("temporary E5 instance must have exactly one boot volume attachment")
        else:
            boot = oci(args.profile, region, "bv", "boot-volume", "get", "--boot-volume-id", boot_attachments[0]["boot-volume-id"])
            if int(boot.get("size-in-gbs", 0)) != 200:
                blockers.append("temporary E5 boot volume must be exactly 200 GiB for adoption")
    for item in instances:
        if item.get("display-name") != names["instance"] and item.get("lifecycle-state") != "TERMINATED":
            unmanaged.append({"kind": "compute-instance", "display_name": item.get("display-name") or "unnamed", "reason": "Additional instance remains outside the single-primary source graph and requires separate cost/rollback ownership."})

    volumes = oci(args.profile, region, "bv", "volume", "list", "--compartment-id", compartment_id, "--all")
    volume = one(volumes, "display-name", names["data_volume"])
    add_import(imports, "data_volume", volume, names["data_volume"])
    if volume and (int(volume.get("size-in-gbs", 0)) != 130 or int(volume.get("vpus-per-gb", 0)) != 0):
        blockers.append("adopted data volume must be 130 GiB at 0 VPUs/GB")
    if volume and instance:
        attachments = oci(args.profile, region, "compute", "volume-attachment", "list", "--compartment-id", compartment_id, "--all")
        attachment = next((item for item in attachments if item.get("volume-id") == volume["id"] and item.get("instance-id") == instance["id"] and item.get("lifecycle-state") != "DETACHED"), None)
        add_import(imports, "data_attachment", attachment, names["data_attachment"])

    vault = one(oci(args.profile, region, "kms", "management", "vault", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["vault"])
    add_import(imports, "vault", vault, names["vault"])
    if vault:
        key = one(oci(args.profile, region, "kms", "management", "key", "list", "--compartment-id", compartment_id, "--endpoint", vault["management-endpoint"], "--all"), "display-name", names["key"])
        add_import(imports, "key", key, names["key"])

    dynamic_groups = oci(args.profile, region, "iam", "dynamic-group", "list", "--compartment-id", tenancy, "--all")
    policies = oci(args.profile, region, "iam", "policy", "list", "--compartment-id", tenancy, "--all")
    add_import(imports, "dynamic_group", one(dynamic_groups, "name", names["dynamic_group"]), names["dynamic_group"])
    add_import(imports, "policy", one(policies, "name", names["policy"]), names["policy"])

    nlbs = oci(args.profile, region, "nlb", "network-load-balancer", "list", "--compartment-id", compartment_id, "--all")
    nlb = one(nlbs, "display-name", names["network_load_balancer"])
    add_import(imports, "nlb", nlb, names["network_load_balancer"])

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
