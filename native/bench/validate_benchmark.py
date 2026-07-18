#!/usr/bin/env python3
"""Validate exact-source OCI A1 direct Rustler benchmark evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "native" / "native-benchmark-v1.schema.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-revision")
    parser.add_argument("--artifact-sha256")
    args = parser.parse_args()

    evidence = load_json(args.evidence)
    schema = load_json(SCHEMA)
    failures = [
        f"schema at {'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(evidence),
            key=lambda error: list(error.absolute_path),
        )
    ]

    latency = evidence.get("latency_us", {})
    if evidence.get("status") == "passed":
        if latency.get("p99", float("inf")) >= latency.get("target_p99", 500):
            failures.append("passed benchmark requires p99 strictly below target_p99")
        if latency.get("maximum", float("inf")) >= latency.get("hard_budget", 1_000):
            failures.append("passed benchmark requires maximum strictly below hard_budget")
        if evidence.get("scheduler_impact", {}).get("status") != "passed":
            failures.append("passed benchmark requires scheduler impact status passed")
        if not evidence.get("fallback_verified"):
            failures.append("passed benchmark requires fallback verification")

    if args.source_revision and evidence.get("source_revision") != args.source_revision:
        failures.append("source revision does not match the requested integration SHA")
    if args.artifact_sha256 and evidence.get("artifact_sha256") != args.artifact_sha256:
        failures.append("artifact SHA256 does not match the requested integration artifact")

    try:
        started = dt.datetime.fromisoformat(evidence["started_at"].replace("Z", "+00:00"))
        completed = dt.datetime.fromisoformat(evidence["completed_at"].replace("Z", "+00:00"))
        if completed < started:
            failures.append("completed_at precedes started_at")
    except (KeyError, TypeError, ValueError):
        failures.append("benchmark timestamps are not parseable ISO-8601 values")

    if failures:
        for failure in failures:
            print(f"FAIL native-benchmark: {failure}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "validation": "native-benchmark-v1",
                "status": "passed",
                "source_revision": evidence["source_revision"],
                "artifact_sha256": evidence["artifact_sha256"],
                "p99_us": latency["p99"],
                "maximum_us": latency["maximum"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
