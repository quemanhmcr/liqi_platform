#!/usr/bin/env python3
"""Validate V1 database recovery assumptions against the integrated OCI output contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASTION_SOURCES = {"10.42.20.100/32", "10.42.20.109/32"}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_v1_host_adapter.py <oci-live-v1.example.json>", file=sys.stderr)
        return 64
    path = Path(sys.argv[1])
    host = json.loads(path.read_text(encoding="utf-8"))
    if host.get("schema_version") != "liqi.infrastructure.oci-live/v1":
        fail("unsupported OCI live contract")

    network = host.get("network", {})
    ingress = {(item.get("protocol"), item.get("port")) for item in network.get("public_ingress", [])}
    if ingress != {("tcp", 80), ("tcp", 443)}:
        fail(f"public ingress changed: {sorted(ingress)}")
    management = network.get("management_access", {})
    if (
        management.get("primary") != "oci-bastion-private-ssh"
        or management.get("secondary") != "oci-run-command"
        or management.get("public_ssh") is not False
    ):
        fail("management access is not private OCI Bastion plus Run Command")
    if set(management.get("exact_source_cidrs", [])) != BASTION_SOURCES:
        fail("management access does not contain exactly the accepted Bastion /32 sources")
    if set(network.get("ssh_source_cidrs", [])) != BASTION_SOURCES:
        fail("host SSH sources do not contain exactly the accepted Bastion /32 sources")
    if network.get("host_public_ip_enabled") is not False or "management_tunnel" in network:
        fail("host management retains a public IP or superseded WireGuard tunnel")

    compute = host.get("host", {})
    if compute.get("public_ipv4") is not None or compute.get("public_ip_mode") != "none":
        fail("database host is not private-only")
    if compute.get("run_command_plugin_enabled") is not True:
        fail("OCI Run Command is unavailable")

    storage = host.get("storage", {})
    if (
        storage.get("application_backup_authority") != "independent-management-storage"
        or storage.get("artifact_archive_authority") != "independent-management-storage"
    ):
        fail("independent recovery authority missing")
    if any("bucket" in key.lower() or "object" in key.lower() for key in storage):
        fail("V1 storage contract retains Object Storage")
    capabilities = host.get("identity", {}).get("capabilities", [])
    if capabilities != ["vault-secret-bundle-read"]:
        fail(f"host identity is broader than Vault read: {capabilities}")
    state = host.get("state_backend", {})
    if state.get("kind") != "postgresql-self-hosted" or state.get("locking") != "postgresql-advisory-locks":
        fail("state backend is not independent PostgreSQL")

    print(
        json.dumps(
            {
                "validation": "database-oci-live-adapter-v1",
                "contract": str(path),
                "backupAuthority": "independent-management-storage",
                "managementAuthority": "oci-bastion-and-run-command",
                "passed": True,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
