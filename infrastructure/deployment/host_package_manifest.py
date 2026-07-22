#!/usr/bin/env python3
"""Validate a signed host package manifest and emit bounded installer settings."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ARCHITECTURES = {
    "aarch64": {
        "pgdg": "common/redhat/rhel-9-aarch64",
        "pgdg_sha256": "c7e6facc5a87018fe138990f3db11e3200f878dd23ffb0d0827b387bc93944ef",
        "caddy": "linux_arm64",
        "otel": "linux_arm64",
        "cosign": "linux-arm64",
        "target_triple": "aarch64-unknown-linux-gnu",
    },
    "x86_64": {
        "pgdg": "common/redhat/rhel-9-x86_64",
        "pgdg_sha256": "02b8767ad537a0003bffb1ff92707d8c30c3bcecab553bb36ebbae22cb83940d",
        "caddy": "linux_amd64",
        "otel": "linux_amd64",
        "cosign": "linux-amd64",
        "target_triple": "x86_64-unknown-linux-gnu",
    },
}
REQUIRED_NAMES = {
    "PostgreSQL", "PgBouncer", "pgBackRest", "Caddy",
    "OpenTelemetry Collector", "OCI CLI", "cosign",
}


def exact_text(item: dict[str, object], key: str, pattern: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"invalid {item.get('name')} {key}")
    return value


def install_names(item: dict[str, object]) -> list[str]:
    values = item.get("install_names")
    if not isinstance(values, list) or not values:
        raise ValueError(f"invalid {item.get('name')} install_names")
    if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.+_-]+", value) for value in values):
        raise ValueError(f"invalid {item.get('name')} install_names")
    return values


def settings(document: dict[str, object], machine: str) -> list[str]:
    architecture = ARCHITECTURES.get(machine)
    if document.get("schema_version") != "liqi.infrastructure.host-packages/v1":
        raise ValueError("unsupported host package manifest")
    if architecture is None or document.get("architecture") != machine:
        raise ValueError("host package manifest architecture mismatch")
    items = document.get("packages")
    if not isinstance(items, list):
        raise ValueError("host package list is invalid")
    by_name = {item.get("name"): item for item in items if isinstance(item, dict)}
    if len(by_name) != len(items) or set(by_name) != REQUIRED_NAMES:
        raise ValueError("host package set or unique names changed")

    postgres = by_name["PostgreSQL"]
    pgbouncer = by_name["PgBouncer"]
    pgbackrest = by_name["pgBackRest"]
    if postgres.get("version") != "17.10" or pgbouncer.get("version") != "1.25.2" or pgbackrest.get("version") != "2.58.0":
        raise ValueError("database package version pin changed")
    rpm_names = install_names(postgres) + install_names(pgbouncer) + install_names(pgbackrest)
    if len(rpm_names) != len(set(rpm_names)):
        raise ValueError("duplicate RPM install name")

    caddy = by_name["Caddy"]
    otel = by_name["OpenTelemetry Collector"]
    cosign = by_name["cosign"]
    if caddy.get("version") != "2.11.3" or otel.get("version") != "0.156.0" or cosign.get("version") != "3.1.2":
        raise ValueError("binary package version pin changed")

    pgdg_url = exact_text(postgres, "source", rf"https://download\.postgresql\.org/pub/repos/yum/{architecture['pgdg']}/pgdg-redhat-repo-42\.0-64\.rhel9PGDG\.noarch\.rpm")
    pgdg_sha256 = exact_text(postgres, "sha256", r"[0-9a-f]{64}")
    if pgdg_sha256 != architecture["pgdg_sha256"]:
        raise ValueError("PGDG repository checksum pin changed")
    pgdg_nevra = exact_text(postgres, "repo_nevra", r"pgdg-redhat-repo-42\.0-64\.rhel9PGDG\.noarch")
    caddy_url = exact_text(caddy, "source", rf"https://github\.com/caddyserver/caddy/releases/download/v2\.11\.3/caddy_2\.11\.3_{architecture['caddy']}\.tar\.gz")
    otel_url = exact_text(otel, "source", rf"https://github\.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0\.156\.0/otelcol_0\.156\.0_{architecture['otel']}\.rpm")
    cosign_url = exact_text(cosign, "source", rf"https://github\.com/sigstore/cosign/releases/download/v3\.1\.2/cosign-{architecture['cosign']}")
    return [
        machine,
        architecture["target_triple"],
        pgdg_url, pgdg_sha256, pgdg_nevra,
        str(caddy["version"]), caddy_url, exact_text(caddy, "sha512", r"[0-9a-f]{128}"),
        str(otel["version"]), otel_url, exact_text(otel, "sha256", r"[0-9a-f]{64}"),
        str(cosign["version"]), cosign_url, exact_text(cosign, "sha256", r"[0-9a-f]{64}"),
        *rpm_names,
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--machine", required=True, choices=tuple(ARCHITECTURES))
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding="utf-8"))
    print("\n".join(settings(document, args.machine)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
