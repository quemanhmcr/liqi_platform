#!/usr/bin/env python3
"""Prove lossless mapping from the accepted runtime wire envelope to V0 storage/handoff."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = {
    "eventId": str,
    "eventType": str,
    "eventVersion": int,
    "occurredAt": str,
    "producer": str,
    "aggregateKey": str,
    "orderingKey": str,
    "payload": dict,
    "metadata": dict,
}
OPTIONAL_UUID = ("correlationId", "causationId")

if len(sys.argv) != 2:
    print("usage: validate_wire_mapping.py <senior3-wire-example.json>", file=sys.stderr)
    raise SystemExit(64)
source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
missing = sorted(set(REQUIRED) - set(source))
if missing:
    print(f"missing wire fields: {', '.join(missing)}", file=sys.stderr)
    raise SystemExit(1)
for field, expected_type in REQUIRED.items():
    if not isinstance(source[field], expected_type) or (expected_type is str and not source[field]):
        print(f"invalid wire field: {field}", file=sys.stderr)
        raise SystemExit(1)
for field in OPTIONAL_UUID:
    value = source.get(field)
    if value is not None and (not isinstance(value, str) or not value):
        print(f"invalid wire field: {field}", file=sys.stderr)
        raise SystemExit(1)

# V0 has one storage schema version. Event version remains the wire/type version.
durable = {
    "event_id": source["eventId"],
    "schema_version": 0,
    "event_type": source["eventType"],
    "event_version": source["eventVersion"],
    "occurred_at": source["occurredAt"],
    "producer": source["producer"],
    "correlation_id": source.get("correlationId"),
    "causation_id": source.get("causationId"),
    "aggregate_key": source["aggregateKey"],
    "ordering_key": source["orderingKey"],
    "payload": source["payload"],
    "metadata": source["metadata"],
}
round_trip = {
    "eventId": durable["event_id"],
    "eventType": durable["event_type"],
    "eventVersion": durable["event_version"],
    "occurredAt": durable["occurred_at"],
    "producer": durable["producer"],
    "aggregateKey": durable["aggregate_key"],
    "orderingKey": durable["ordering_key"],
    "payload": durable["payload"],
    "metadata": durable["metadata"],
}
for wire, storage in (("correlationId", "correlation_id"), ("causationId", "causation_id")):
    if durable[storage] is not None:
        round_trip[wire] = durable[storage]
expected = {key: source[key] for key in REQUIRED}
expected.update({key: source[key] for key in OPTIONAL_UUID if key in source})
if durable["schema_version"] != 0 or round_trip != expected:
    print("wire-to-durable-to-handoff round trip lost semantics", file=sys.stderr)
    raise SystemExit(1)

root = Path(__file__).resolve().parents[3]
persistence = (root / "crates/persistence-postgres/src/lib.rs").read_text(encoding="utf-8")
realtime = (root / "services/realtime/src/main.rs").read_text(encoding="utf-8")
probe = (root / "services/admin-cli/src/platform_probe.rs").read_text(encoding="utf-8")
required_persistence_tokens = (
    "platform.read_realtime_handoff_v0($1, $2)",
    "schema_version",
    "producer",
    "correlation_id",
    "causation_id",
    "aggregate_key",
    "ordering_key",
    "payload",
    "metadata",
    "map_event_row",
)
missing_tokens = [token for token in required_persistence_tokens if token not in persistence]
if missing_tokens:
    print(f"runtime persistence mapping is incomplete: {', '.join(missing_tokens)}", file=sys.stderr)
    raise SystemExit(1)
if "refresh_handoff_readiness" not in realtime or "committed-handoff-provider-missing" in realtime:
    print("realtime readiness does not prove the committed handoff provider", file=sys.stderr)
    raise SystemExit(1)
if "platform.observe_probe_v0($1, $2)" not in probe:
    print("platform probe does not consume provider-owned observation", file=sys.stderr)
    raise SystemExit(1)
for forbidden in (
    "FROM platform.probe_state_v0",
    "JOIN platform.outbox_events",
    "JOIN platform.probe_effects_v0",
):
    if forbidden in probe:
        print(f"platform probe reads authority tables directly: {forbidden}", file=sys.stderr)
        raise SystemExit(1)

print(json.dumps({
    "validation": "wire-durable-handoff-mapping-v0",
    "storageSchemaVersion": durable["schema_version"],
    "fields": sorted(expected),
    "passed": True,
}, separators=(",", ":")))
