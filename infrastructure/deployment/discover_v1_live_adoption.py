#!/usr/bin/env python3
"""Discover compatible existing OCI resources for a no-mutation V1 state adoption manifest."""
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

ADDRESSES = {
    "compartment": ("module.v1_live.oci_identity_compartment.environment", "oci_identity_compartment"),
    "vcn": ("module.v1_live.oci_core_vcn.main", "oci_core_vcn"),
    "internet_gateway": ("module.v1_live.oci_core_internet_gateway.main", "oci_core_internet_gateway"),
    "route_table": ("module.v1_live.oci_core_route_table.edge", "oci_core_route_table"),
    "security_list": ("module.v1_live.oci_core_security_list.empty", "oci_core_security_list"),
    "subnet": ("module.v1_live.oci_core_subnet.edge", "oci_core_subnet"),
    "nsg": ("module.v1_live.oci_core_network_security_group.host", "oci_core_network_security_group"),
    "instance": ("module.v1_live.oci_core_instance.host", "oci_core_instance"),
    "data_volume": ("module.v1_live.oci_core_volume.data", "oci_core_volume"),
    "data_attachment": ("module.v1_live.oci_core_volume_attachment.data", "oci_core_volume_attachment"),
    "vault": ("module.v1_live.oci_kms_vault.main", "oci_kms_vault"),
    "key": ("module.v1_live.oci_kms_key.main", "oci_kms_key"),
    "dynamic_group": ("module.v1_live.oci_identity_dynamic_group.host", "oci_identity_dynamic_group"),
    "policy": ("module.v1_live.oci_identity_policy.host", "oci_identity_policy"),
}


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
    return json.loads(completed.stdout).get("data")


def one(items: list[dict[str, Any]], field: str, value: str) -> dict[str, Any] | None:
    matches = [item for item in items if item.get(field) == value and item.get("lifecycle-state") not in {"DELETED", "TERMINATED"}]
    if len(matches) > 1:
        raise RuntimeError(f"multiple active resources match {field}={value!r}")
    return matches[0] if matches else None


def add_import(records: list[dict[str, str]], key: str, item: dict[str, Any] | None, display_name: str) -> None:
    if not item:
        return
    address, resource_type = ADDRESSES[key]
    records.append({"address": address, "id": item["id"], "resource_type": resource_type, "display_name": display_name})


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
    if set(names) != set(ADDRESSES) | {"reserved_public_ip"}:
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

    vcns = oci(args.profile, region, "network", "vcn", "list", "--compartment-id", compartment_id, "--all")
    vcn = one(vcns, "display-name", names["vcn"])
    add_import(imports, "vcn", vcn, names["vcn"])
    if vcn and (vcn.get("cidr-blocks") or []) != ["10.42.0.0/16"]:
        blockers.append("adopted VCN must be exactly 10.42.0.0/16")
    vcn_id = vcn.get("id") if vcn else None

    if vcn_id:
        igw = one(oci(args.profile, region, "network", "internet-gateway", "list", "--compartment-id", compartment_id, "--all"), "display-name", names["internet_gateway"])
        routes = oci(args.profile, region, "network", "route-table", "list", "--compartment-id", compartment_id, "--all")
        route = one(routes, "display-name", names["route_table"])
        security_lists = oci(args.profile, region, "network", "security-list", "list", "--compartment-id", compartment_id, "--vcn-id", vcn_id, "--all")
        security_list = one(security_lists, "display-name", names["security_list"])
        subnets = oci(args.profile, region, "network", "subnet", "list", "--compartment-id", compartment_id, "--all")
        subnet = one(subnets, "display-name", names["subnet"])
        nsgs = oci(args.profile, region, "network", "nsg", "list", "--compartment-id", compartment_id, "--all")
        nsg = one(nsgs, "display-name", names["nsg"])
        for key, item in (("internet_gateway", igw), ("route_table", route), ("security_list", security_list), ("subnet", subnet), ("nsg", nsg)):
            if item and item.get("vcn-id") != vcn_id:
                blockers.append(f"{key} does not belong to the adopted VCN")
            else:
                add_import(imports, key, item, names[key])
        if igw and not igw.get("is-enabled"):
            blockers.append("adopted internet gateway is disabled")
        if subnet and (subnet.get("cidr-block") != "10.42.10.0/24" or subnet.get("prohibit-public-ip-on-vnic")):
            blockers.append("adopted public subnet must be 10.42.10.0/24 and allow public IP assignment")
        for item in nsgs:
            if item.get("display-name") != names["nsg"]:
                unmanaged.append({"kind": "network-security-group", "display_name": item.get("display-name") or "unnamed", "reason": "Existing transition NSG remains outside the source-managed no-SSH boundary."})

    instances = oci(args.profile, region, "compute", "instance", "list", "--compartment-id", compartment_id, "--all")
    instance = one(instances, "display-name", names["instance"])
    add_import(imports, "instance", instance, names["instance"])
    if instance:
        if instance.get("compartment-id") != compartment_id:
            blockers.append("temporary E5 instance is outside the adopted compartment")
        shape = instance.get("shape-config") or {}
        if instance.get("shape") != "VM.Standard.E5.Flex" or shape.get("ocpus") != 4 or shape.get("memory-in-gbs") != 24:
            blockers.append("temporary E5 instance must be VM.Standard.E5.Flex at exactly 4 OCPU/24 GiB")
        boot_attachments = oci(args.profile, region, "compute", "boot-volume-attachment", "list", "--compartment-id", compartment_id, "--instance-id", instance["id"], "--availability-domain", instance["availability-domain"])
        if boot_attachments:
            boot = oci(args.profile, region, "bv", "boot-volume", "get", "--boot-volume-id", boot_attachments[0]["boot-volume-id"])
            if int(boot.get("size-in-gbs", 0)) != 200:
                blockers.append("temporary E5 boot volume must be exactly 200 GiB for adoption")
    for item in instances:
        if item.get("display-name") != names["instance"] and item.get("lifecycle-state") != "TERMINATED":
            unmanaged.append({"kind": "compute-instance", "display_name": item.get("display-name") or "unnamed", "reason": "Additional instance remains outside the single-host source graph and requires separate cost/rollback ownership."})

    volumes = oci(args.profile, region, "bv", "volume", "list", "--compartment-id", compartment_id, "--all")
    volume = one(volumes, "display-name", names["data_volume"])
    add_import(imports, "data_volume", volume, names["data_volume"])
    if volume and (int(volume.get("size-in-gbs", 0)) != 130 or int(volume.get("vpus-per-gb", 0)) != 0):
        blockers.append("adopted data volume must be 130 GiB at 0 VPUs/GB")
    if volume and instance:
        attachments = oci(args.profile, region, "compute", "volume-attachment", "list", "--compartment-id", compartment_id, "--all")
        attachment = next((item for item in attachments if item.get("volume-id") == volume["id"] and item.get("instance-id") == instance["id"] and item.get("lifecycle-state") != "DETACHED"), None)
        add_import(imports, "data_attachment", attachment, names["data_attachment"])

    vaults = oci(args.profile, region, "kms", "management", "vault", "list", "--compartment-id", compartment_id, "--all")
    vault = one(vaults, "display-name", names["vault"])
    add_import(imports, "vault", vault, names["vault"])
    if vault:
        keys = oci(args.profile, region, "kms", "management", "key", "list", "--compartment-id", compartment_id, "--endpoint", vault["management-endpoint"], "--all")
        key = one(keys, "display-name", names["key"])
        add_import(imports, "key", key, names["key"])

    dynamic_groups = oci(args.profile, region, "iam", "dynamic-group", "list", "--compartment-id", tenancy, "--all")
    policies = oci(args.profile, region, "iam", "policy", "list", "--compartment-id", tenancy, "--all")
    add_import(imports, "dynamic_group", one(dynamic_groups, "name", names["dynamic_group"]), names["dynamic_group"])
    add_import(imports, "policy", one(policies, "name", names["policy"]), names["policy"])

    imported_addresses = {item["address"] for item in imports}
    missing = sorted(address for key, (address, _kind) in ADDRESSES.items() if address not in imported_addresses)
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
