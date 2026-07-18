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

compatibility = runtime["compatibility"]
if compatibility["ectoSql"] != ">=3.14,<3.15":
    errors.append("Ecto SQL must remain on the audited 3.14 line")
if compatibility["postgrex"] != ">=0.22.3,<0.23":
    errors.append("Postgrex must remain at or above the 0.22.3 security floor")
if compatibility["decimal"] != ">=3.0,<4.0":
    errors.append("Decimal must remain outside the vulnerable pre-3.0 range")

seams = runtime["callableSeams"]
expected_signatures = {
    "readiness": "platform.database_readiness_v1(bigint,integer)",
    "requestProbe": "platform.request_probe_v1(uuid,uuid,text,text,text,bigint,timestamptz,uuid,uuid,jsonb,timestamptz,jsonb)",
    "observeProbe": "platform.observe_probe_v1(uuid,uuid)",
    "claimOutbox": "platform.claim_outbox_v1(text,integer,integer)",
    "applyProbeEffect": "platform.apply_probe_effect_and_ack_v1(uuid,uuid,text)",
    "failOutbox": "platform.fail_outbox_v1(uuid,uuid,text,text,timestamptz)",
    "readHandoff": "platform.read_realtime_handoff_v1(bigint,integer)",
}
for name, signature in expected_signatures.items():
    if seams.get(name, {}).get("signature") != signature:
        errors.append(f"callable signature changed for {name}")

required_columns = {
    "requestProbe": {"probe_id", "event_id", "aggregate_version", "handoff_cursor", "duplicate", "status", "outcome"},
    "observeProbe": {"probe_id", "event_id", "aggregate_version", "probe_status", "outbox_state", "effect_applied", "handoff_cursor", "terminal", "observed_at"},
    "claimOutbox": {"event_type", "aggregate_key", "payload_type", "actor_key", "event_version", "payload_version"},
    "readHandoff": {"handoff_id", "event_type", "aggregate_key", "payload_type", "actor_key", "event_version", "payload_version"},
}
for seam_name, columns in required_columns.items():
    observed = set(seams.get(seam_name, {}).get("resultColumns", []))
    if not columns <= observed:
        errors.append(f"{seam_name} result columns do not satisfy the runtime callback: {sorted(columns - observed)}")

if seams["requestProbe"].get("errors") != {
    "idempotencyConflict": "LQ001",
    "staleAggregateVersion": "LQ002",
}:
    errors.append("command conflict SQLSTATE mapping changed")
if seams["observeProbe"].get("errors") != {
    "notFound": "empty-result",
    "identityMismatch": "LQ003",
}:
    errors.append("probe observation not-found/identity-mismatch semantics changed")
if seams["readHandoff"].get("errors") != {"cursorGap": "LQ004"}:
    errors.append("realtime cursor gap SQLSTATE mapping changed")

consumer = runtime["consumerIntegration"]
expected_callbacks = [
    "readiness/1",
    "request_probe/1",
    "observe_probe/2",
    "claim_probe_events/2",
    "apply_probe_effect/3",
    "fail_event/5",
    "read_handoff/2",
]
if consumer != {
    "module": "LiqiPersistence.RuntimeAdapter",
    "callbacks": expected_callbacks,
    "repoOwnership": "runtime-configured-single-pool-set",
    "commandIdentity": "consumer-module-event_id/1",
    "providerStartupDefault": "disabled-until-runtime-owner-configures-supervision",
}:
    errors.append("Senior 1 persistence consumer integration contract changed")

if outbox["delivery"] != "at-least-once" or "exactly" in json.dumps(outbox).lower():
    errors.append("outbox must claim only at-least-once delivery")
if handoff["delivery"] != "at-least-once-idempotent-consumer":
    errors.append("realtime handoff delivery semantics invalid")
if idempotency["aggregateVersion"]["stale"] != "LQ002":
    errors.append("stale aggregate error code must be LQ002")
fingerprint = idempotency["requestFingerprint"]
if fingerprint != {
    "algorithm": "sha256",
    "encoding": "lowercase-hex",
    "length": 64,
    "ownership": "consumer-command-module",
    "input": "consumer-versioned-stable-command-representation",
    "databaseBehavior": "opaque-exact-compare",
    "interoperability": "not-a-cross-language-canonical-json-contract",
}:
    errors.append("request fingerprint ownership or opaque comparison semantics changed")
deadline = outbox["envelope"]["deadlineSemantics"]
if deadline != {
    "consumerUnit": "unix-epoch-milliseconds",
    "storedType": "timestamptz",
    "conversionOwner": "LiqiPersistence.RuntimeAdapter",
    "expiredCommandAdmission": "reject-before-transaction",
}:
    errors.append("runtime/database deadline conversion semantics changed")
callbacks = runtime["consumerIntegration"]["callbacks"]
if callbacks != [
    "readiness/1",
    "request_probe/1",
    "observe_probe/2",
    "claim_probe_events/2",
    "apply_probe_effect/3",
    "fail_event/5",
    "read_handoff/2",
]:
    errors.append("Senior 1 persistence callback surface changed")
if idempotency["fingerprint"] != {
    "source": "consumer-supplied-logical-command",
    "algorithm": "sha256",
    "encoding": "lowercase-hex",
    "length": 64,
    "storage": "validated-verbatim",
}:
    errors.append("idempotency fingerprint semantics changed")
expected_aliases = {"actor_key": "aggregate_key", "payload_type": "event_type", "payload_version": "event_version"}
if outbox["consumerAliases"] != expected_aliases or handoff["consumerAliases"] != expected_aliases:
    errors.append("outbox/handoff consumer aliases diverge")
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
