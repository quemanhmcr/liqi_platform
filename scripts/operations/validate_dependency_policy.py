#!/usr/bin/env python3
"""Validate dependency policy and reject contradictory or expired exceptions."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "operations" / "dependency-policy-v0.schema.json"
DEFAULT_POLICY = ROOT / "operations" / "release" / "dependency-policy-v0.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    failures = [
        f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(policy),
            key=lambda item: list(item.absolute_path),
        )
    ]
    allowed = set(policy.get("licenses", {}).get("allow", []))
    denied = set(policy.get("licenses", {}).get("deny", []))
    overlap = sorted(allowed & denied)
    if overlap:
        failures.append(f"licenses cannot be both allowed and denied: {overlap}")
    for exception in policy.get("exceptions", []):
        expiry = date.fromisoformat(exception["expires_at"])
        if expiry < args.as_of:
            failures.append(f"expired dependency exception: {exception['package']} expired {expiry}")
        if not (ROOT / exception["decision_note"]).is_file():
            failures.append(f"exception decision note is missing: {exception['decision_note']}")
    if failures:
        for failure in failures:
            print(f"ERROR dependency-policy: {failure}", file=sys.stderr)
        return 1
    print(
        f"validated dependency policy: {len(allowed)} allowed licenses, "
        f"{len(denied)} denied licenses, {len(policy['exceptions'])} exceptions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
