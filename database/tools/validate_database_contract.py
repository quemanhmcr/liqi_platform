#!/usr/bin/env python3
"""Validate the V0 database provider contract without exposing secrets."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/platform/database-v0.schema.json"
EXAMPLE_PATH = ROOT / "contracts/platform/database-v0.example.json"

FORBIDDEN_KEY_RE = re.compile(r"(^|_)(password|passwd|dsn|private_key|secret_value|token)($|_)", re.IGNORECASE)
FORBIDDEN_VALUE_RE = re.compile(r"postgres(?:ql)?://[^\s]+:[^\s]+@", re.IGNORECASE)


def walk(value: object, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if FORBIDDEN_KEY_RE.search(key) and key not in {
                "secretReferences",
                "backupRepositorySecret",
                "backupCipherPassphrase",
                "token",  # outbox claim-token semantic, never credential material
            }:
                errors.append(f"{path}.{key}: forbidden plaintext-secret-shaped key")
            errors.extend(walk(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(walk(child, f"{path}[{index}]"))
    elif isinstance(value, str) and FORBIDDEN_VALUE_RE.search(value):
        errors.append(f"{path}: plaintext PostgreSQL DSN is forbidden")
    return errors


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    failures = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(example), key=lambda item: list(item.absolute_path))
    ]
    failures.extend(walk(example))

    roles = {role["name"] for role in example["roles"]}
    required_roles = {
        "liqi_owner", "liqi_migrator", "liqi_api", "liqi_realtime",
        "liqi_worker", "liqi_readonly", "liqi_monitor", "liqi_backup",
    }
    missing_roles = sorted(required_roles - roles)
    if missing_roles:
        failures.append(f"missing required roles: {', '.join(missing_roles)}")

    budget = example["connectionBudget"]
    allocated = (
        budget["runtimeServerConnections"]
        + budget["operationalPoolConnections"]
        + budget["directAdministrativeConnections"]
        + budget["reservedHeadroom"]
    )
    if allocated != budget["postgresMaxConnections"]:
        failures.append(
            "connection budget must exactly partition postgresMaxConnections: "
            f"{allocated} != {budget['postgresMaxConnections']}"
        )

    role_caps = budget["poolRoleCaps"]
    expected_role_caps = {
        "liqi_api": 20, "liqi_realtime": 5, "liqi_worker": 10,
        "liqi_readonly": 3, "liqi_monitor": 2,
    }
    if role_caps != expected_role_caps:
        failures.append(f"PgBouncer role caps differ from V0 hard allocation: {role_caps}")
    if sum(role_caps[name] for name in ("liqi_api", "liqi_realtime", "liqi_worker")) != 35:
        failures.append("runtime PgBouncer role caps must total 35")
    if sum(role_caps[name] for name in ("liqi_readonly", "liqi_monitor")) != 5:
        failures.append("operational PgBouncer role caps must total 5")
    if example["migration"]["requiredVersion"] != 4 or example["health"]["requiredMigrationVersion"] != 4:
        failures.append("all V0 consumers must fail closed against required migration version 4")
    if example["realtimeHandoff"]["readFunction"] != "platform.read_realtime_handoff_v0(bigint,integer)":
        failures.append("committed realtime handoff reader function changed")
    if example["probeObservation"]["function"] != "platform.observe_probe_v0(uuid,uuid)":
        failures.append("provider-owned probe observation function changed")
    if example["probeObservation"]["directTableAccess"] is not False:
        failures.append("probe observation must not grant direct table access")

    required_wire = {
        "eventId", "eventType", "eventVersion", "occurredAt",
        "aggregateKey", "orderingKey", "payload",
    }
    actual_wire = set(example["outbox"]["wireMappingRequiredFields"])
    if actual_wire != required_wire:
        failures.append("wire mapping fields must preserve the complete V0 envelope seam")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1

    print("database-v0 contract: valid")
    print(f"postgresql-major: {example['compatibility']['postgresql']['major']}")
    print(f"pool-mode: {example['outbox']['delivery']} via transaction-pooled runtime boundary")
    print(f"roles: {len(example['roles'])}")
    print("plaintext-secrets: none detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
