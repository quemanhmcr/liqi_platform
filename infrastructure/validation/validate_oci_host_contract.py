#!/usr/bin/env python3
"""Validate the OCI host example against the provider-owned JSON Schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # pragma: no cover - explicit operator guidance
    raise SystemExit(
        "jsonschema is required; install with: "
        "python -m pip install -r infrastructure/validation/requirements.txt"
    ) from exc

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/platform/oci-host-v0.schema.json"
EXAMPLE_PATH = ROOT / "contracts/platform/oci-host-v0.example.json"


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to load {path.relative_to(ROOT)}: {exc}") from exc


def main() -> int:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(example), key=lambda error: list(error.path))
    if errors:
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            print(f"ERROR {location}: {error.message}", file=sys.stderr)
        return 1

    assert isinstance(example, dict)
    semantic_errors: list[str] = []

    capacity = example["capacity_profile"]
    expected_capacity = {
        "name": "free-tier-a1-4x24",
        "shape": "VM.Standard.A1.Flex",
        "architecture": "aarch64",
        "ocpus": 4,
        "memory_gb": 24,
    }
    for key, expected in expected_capacity.items():
        if capacity.get(key) != expected:
            semantic_errors.append(
                f"capacity_profile.{key} must be {expected!r}, got {capacity.get(key)!r}"
            )

    services = {item["service"]: item for item in example["identities"]["services"]}
    expected_services = {
        "liqi-api": ("liqi-api", 2210),
        "liqi-realtime": ("liqi-realtime", 2211),
        "liqi-worker": ("liqi-worker", 2212),
    }
    if set(services) != set(expected_services):
        semantic_errors.append(
            f"service identities must be exactly {sorted(expected_services)}, got {sorted(services)}"
        )
    else:
        for service, (user, uid) in expected_services.items():
            if services[service]["user"] != user or services[service]["uid"] != uid:
                semantic_errors.append(
                    f"{service} identity must be user={user!r}, uid={uid}"
                )

    required_directories = {
        "/opt/liqi/releases",
        "/opt/liqi",
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
    directories = {entry["path"]: entry for entry in example["directories"]}
    missing_directories = required_directories - set(directories)
    if missing_directories:
        semantic_errors.append(f"missing required directories: {sorted(missing_directories)}")
    for path_value, directory in directories.items():
        if int(directory["mode"], 8) & 0o002:
            semantic_errors.append(f"directory {path_value} is world-writable")

    expected_ports = {
        "edge-http": (80, "public-redirect-only", True),
        "edge-https": (443, "public-edge", True),
        "administration-ssh": (22, "admin-allowlist-only", False),
        "postgresql": (5432, "host-internal-only", False),
        "pgbouncer": (6432, "host-internal-only", False),
        "liqi-api": (8080, "host-internal-only", False),
        "liqi-realtime": (8081, "host-internal-only", False),
        "liqi-worker-admin": (8082, "host-internal-only", False),
        "otel-otlp-grpc": (4317, "host-internal-only", False),
        "otel-otlp-http": (4318, "host-internal-only", False),
    }
    ports = {entry["name"]: entry for entry in example["ports"]}
    if set(ports) != set(expected_ports):
        semantic_errors.append(
            f"port names must be exactly {sorted(expected_ports)}, got {sorted(ports)}"
        )
    else:
        for name, (port, exposure, enabled) in expected_ports.items():
            actual = ports[name]
            if (actual["port"], actual["exposure"], actual["default_enabled"]) != (
                port,
                exposure,
                enabled,
            ):
                semantic_errors.append(
                    f"port contract mismatch for {name}: expected "
                    f"{(port, exposure, enabled)!r}"
                )

    forbidden_key_fragments = (
        "password",
        "private_key",
        "private-key",
        "pem",
        "secret_value",
        "secret-value",
        "access_token",
        "refresh_token",
    )

    def inspect_keys(value: object, path_parts: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = key.lower()
                if any(fragment in lowered for fragment in forbidden_key_fragments):
                    semantic_errors.append(
                        "secret-bearing field is forbidden in host output: "
                        + ".".join((*path_parts, key))
                    )
                inspect_keys(child, (*path_parts, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_keys(child, (*path_parts, str(index)))

    inspect_keys(example)

    if semantic_errors:
        for error in semantic_errors:
            print(f"ERROR semantic: {error}", file=sys.stderr)
        return 1

    print(
        "validated contracts/platform/oci-host-v0.example.json "
        "against schema and provider semantics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
