#!/usr/bin/env python3
"""Validate database assumptions against the Senior 1 OCI host V0 example."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_oci_host_adapter.py <oci-host-v0.example.json>", file=sys.stderr)
        return 64
    path = Path(sys.argv[1])
    host = json.loads(path.read_text(encoding="utf-8"))
    if host.get("schema_version") != "liqi.platform.oci-host/v0":
        fail("unsupported OCI host contract version")
    directories = {item["purpose"]: item for item in host.get("directories", [])}
    ports = {item["name"]: item for item in host.get("ports", [])}
    object_refs = {item["purpose"]: item for item in host.get("object_storage_references", [])}

    expected_directories = {
        "postgresql-data": ("/var/lib/liqi/postgresql/data", "postgres"),
        "postgresql-backup-staging": ("/var/lib/liqi/postgresql/backup-staging", "postgres"),
        "secrets-materialization": ("/run/liqi/secrets", "root"),
    }
    for purpose, (expected_path, expected_owner) in expected_directories.items():
        actual = directories.get(purpose)
        if not actual:
            fail(f"host directory missing: {purpose}")
        if actual.get("path") != expected_path or actual.get("owner") != expected_owner:
            fail(f"host directory changed for {purpose}: {actual}")

    for name, port in (("postgresql", 5432), ("pgbouncer", 6432)):
        actual = ports.get(name)
        if not actual or actual.get("port") != port or actual.get("bind_scope") != "loopback":
            fail(f"database port contract changed for {name}: {actual}")

    backup = object_refs.get("postgresql-backups")
    if not backup:
        fail("postgresql-backups Object Storage output missing")
    if backup.get("prefix") != "postgresql/" or backup.get("access_path") != "service-gateway":
        fail(f"PostgreSQL backup Object Storage seam changed: {backup}")

    secret_template = host.get("secret_reference_format", {}).get("uri_template")
    if secret_template != "oci-vault://<secret-ocid>@<version-or-CURRENT>":
        fail("OCI Vault secret-reference format changed")
    if host.get("release_target", {}).get("current_symlink") != "/opt/liqi/current":
        fail("stable release symlink changed")
    if host.get("readiness", {}).get("file") != "/run/liqi/host-ready.json":
        fail("host readiness output path changed")

    print(json.dumps({
        "validation": "database-oci-host-adapter-v0",
        "contract": str(path),
        "passed": True,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
