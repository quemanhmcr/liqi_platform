#!/usr/bin/env python3
"""Validate SLO, alert and runbook cross-references."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "operations" / "slo-catalog-v0.schema.json"
CATALOG = ROOT / "operations" / "slo" / "slo-catalog-v0.json"
ALERTS = ROOT / "operations" / "alerts" / "alert-policy-v0.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    schema = load(SCHEMA)
    catalog = load(CATALOG)
    alerts = load(ALERTS)
    failures = [
        f"{'.'.join(map(str, error.absolute_path))}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(catalog),
            key=lambda item: list(item.absolute_path),
        )
    ]
    ids: set[str] = set()
    for entry in [*catalog["slos"], *catalog["correctness_events"]]:
        if entry["id"] in ids:
            failures.append(f"duplicate SLO/correctness ID: {entry['id']}")
        ids.add(entry["id"])
        runbook = ROOT / entry["runbook"]
        if not runbook.is_file():
            failures.append(f"missing runbook for {entry['id']}: {entry['runbook']}")
        source = entry["sli_source"].lower()
        if "average latency" in source or "mean latency" in source:
            failures.append(f"{entry['id']} uses forbidden average latency source")
        objective = entry.get("objective")
        if objective and objective["kind"] == "percentile" and objective.get("percentile") not in {"p95", "p99"}:
            failures.append(f"{entry['id']} latency percentile must be p95 or p99")
    for event in catalog["correctness_events"]:
        if event["budget"] != 0 or event["tolerance"] != "zero":
            failures.append(f"correctness event {event['id']} must have zero budget")
    if alerts["principles"]["cpu_spike_alone_pages"]:
        failures.append("CPU spike alone must not page")
    if alerts["principles"]["average_latency_is_primary"]:
        failures.append("average latency cannot be the primary alert")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"validated {len(catalog['slos'])} working SLOs and {len(catalog['correctness_events'])} correctness events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
