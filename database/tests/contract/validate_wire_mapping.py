#!/usr/bin/env python3
"""Prove lossless mapping from a Senior 3 wire-envelope example to V0 storage."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = {
    "eventId": str,
    "eventType": str,
    "eventVersion": int,
    "occurredAt": str,
    "aggregateKey": str,
    "orderingKey": str,
    "payload": dict,
}

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

durable = {
    "event_id": source["eventId"],
    "event_type": source["eventType"],
    "event_version": source["eventVersion"],
    "occurred_at": source["occurredAt"],
    "aggregate_key": source["aggregateKey"],
    "ordering_key": source["orderingKey"],
    "payload": source["payload"],
}
round_trip = {
    "eventId": durable["event_id"],
    "eventType": durable["event_type"],
    "eventVersion": durable["event_version"],
    "occurredAt": durable["occurred_at"],
    "aggregateKey": durable["aggregate_key"],
    "orderingKey": durable["ordering_key"],
    "payload": durable["payload"],
}
expected = {key: source[key] for key in REQUIRED}
if round_trip != expected:
    print("wire-to-durable round trip lost semantics", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps({"validation":"wire-durable-mapping-v0","fields":sorted(REQUIRED),"passed":True}, separators=(",", ":")))
