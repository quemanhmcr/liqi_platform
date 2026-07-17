#!/usr/bin/env python3
"""Evaluate Senior 2 recovery evidence against Senior 4 freshness policy."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "operations" / "recovery-status-v0.schema.json"
POLICY = ROOT / "operations" / "disaster-recovery" / "recovery-policy-v0.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--as-of", help="RFC3339 instant; defaults to current UTC")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    document = load(args.status)
    schema = load(SCHEMA)
    policy = load(args.policy)
    failures = [
        f"{'.'.join(map(str, error.absolute_path))}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]
    now = instant(args.as_of) if args.as_of else datetime.now(tz=timezone.utc)
    backup_age = max(0, int((now - instant(document["backup"]["completed_at"])).total_seconds()))
    restore_age = max(0, int((now - instant(document["restore_verification"]["verified_at"])).total_seconds()))
    wal_lag = int(document["wal_archive"]["lag_seconds"])

    if backup_age > policy["backup_max_age_seconds"]:
        failures.append(f"backup age {backup_age}s exceeds {policy['backup_max_age_seconds']}s")
    if wal_lag > policy["wal_archive_max_lag_seconds"]:
        failures.append(f"WAL archive lag {wal_lag}s exceeds {policy['wal_archive_max_lag_seconds']}s")
    if restore_age > policy["restore_verification_max_age_seconds"]:
        failures.append(f"restore verification age {restore_age}s exceeds {policy['restore_verification_max_age_seconds']}s")
    if document["restore_verification"]["rpo_observed_seconds"] > policy["rpo_target_seconds"]:
        failures.append("observed restore RPO exceeds target")
    if document["restore_verification"]["rto_observed_seconds"] > policy["rto_target_seconds"]:
        failures.append("observed restore RTO exceeds target")

    result = {
        "schema_version": "recovery-freshness-result-v0",
        "status": "failed" if failures else "passed",
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "backup_completed_at": document["backup"]["completed_at"],
        "backup_age_seconds": backup_age,
        "wal_archive_lag_seconds": wal_lag,
        "restore_verified_at": document["restore_verification"]["verified_at"],
        "restore_verification_age_seconds": restore_age,
        "rpo_observed_seconds": document["restore_verification"]["rpo_observed_seconds"],
        "rto_observed_seconds": document["restore_verification"]["rto_observed_seconds"],
        "evidence_ref": document["restore_verification"]["evidence_ref"],
        "failures": failures,
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    print(encoded, end="")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
