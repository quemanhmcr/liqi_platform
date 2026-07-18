#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3]
PAIRS = [
    ("contracts/database/database-runtime-v1.schema.json", "contracts/database/database-runtime-v1.example.json"),
    ("contracts/database/migration-readiness-v1.schema.json", "contracts/database/migration-readiness-v1.example.json"),
    ("contracts/database/outbox-v1.schema.json", "contracts/database/outbox-v1.example.json"),
    ("contracts/database/realtime-handoff-v1.schema.json", "contracts/database/realtime-handoff-v1.example.json"),
    ("contracts/database/idempotency-v1.schema.json", "contracts/database/idempotency-v1.example.json"),
    ("contracts/jobs/oban-policy-v1.schema.json", "contracts/jobs/oban-policy-v1.example.json"),
    ("contracts/database/recovery-status-v1.schema.json", "contracts/database/recovery-status-v1.example.json"),
]

def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))

errors: list[str] = []
for schema_path, example_path in PAIRS:
    schema = load(schema_path)
    example = load(example_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(validator.iter_errors(example), key=lambda item: list(item.path)):
        errors.append(f"{example_path}:{'.'.join(map(str, error.path))}: {error.message}")

runtime = load(PAIRS[0][1])
readiness = load(PAIRS[1][1])
outbox = load(PAIRS[2][1])
handoff = load(PAIRS[3][1])
idempotency = load(PAIRS[4][1])
oban = load(PAIRS[5][1])
recovery = load(PAIRS[6][1])

required_versions = {runtime["requiredMigration"], readiness["requiredVersion"], recovery["requiredMigrationVersion"]}
if required_versions != {8}:
    errors.append(f"required migration versions diverge: {sorted(required_versions)}")

pool_sum = sum(runtime["connectionBudget"]["ectoPools"].values())
if pool_sum > runtime["connectionBudget"]["pgbouncerServerCapacity"]:
    errors.append("Ecto pool demand exceeds PgBouncer server capacity")
for pool, role in (("command", "liqi_api"), ("realtime", "liqi_realtime"), ("jobs", "liqi_worker")):
    if runtime["connectionBudget"]["ectoPools"][pool] > runtime["connectionBudget"]["poolRoleCaps"][role]:
        errors.append(f"{pool} Ecto pool exceeds {role} cap")

if runtime["connectionBudget"]["pgbouncerServerCapacity"] + runtime["connectionBudget"]["directReservation"] + runtime["connectionBudget"]["reservedHeadroom"] != runtime["connectionBudget"]["postgresMaxConnections"]:
    errors.append("PostgreSQL connection accounting does not close exactly")

if outbox["delivery"] != "at-least-once" or "exactly" in json.dumps(outbox).lower():
    errors.append("outbox must claim only at-least-once delivery")
if handoff["delivery"] != "at-least-once-idempotent-consumer":
    errors.append("realtime handoff delivery semantics invalid")
if idempotency["aggregateVersion"]["stale"] != "LQ002":
    errors.append("stale aggregate error code must be LQ002")
if oban["compatibility"]["migrationVersion"] != 14 or oban["storage"]["prefix"] != "oban":
    errors.append("Oban storage version/prefix mismatch")
if sum(queue["concurrency"] for queue in oban["queues"]) != 7:
    errors.append("Oban queue concurrency total must remain 7 including paused recovery")
if not next(queue for queue in oban["queues"] if queue["name"] == "recovery")["pausedByDefault"]:
    errors.append("recovery queue must be paused by default")
if recovery["targets"] != {"rpoSeconds": 300, "rtoSeconds": 3600}:
    errors.append("recovery targets changed")

all_text = "\n".join((ROOT / path).read_text(encoding="utf-8") for pair in PAIRS for path in pair)
for pattern in (r"postgres(?:ql)?://[^\s\"]+:[^\s\"]+@", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"):
    if re.search(pattern, all_text, re.IGNORECASE):
        errors.append(f"secret-shaped material found: {pattern}")

if errors:
    for error in errors:
        print(f"ERROR: {error}")
    raise SystemExit(1)

print(json.dumps({
    "validation": "database-contracts-v1",
    "contracts": len(PAIRS),
    "requiredMigration": 8,
    "obanMigration": 14,
    "ectoClientPools": pool_sum,
    "pgbouncerServerCapacity": runtime["connectionBudget"]["pgbouncerServerCapacity"],
    "passed": True,
}, separators=(",", ":")))
