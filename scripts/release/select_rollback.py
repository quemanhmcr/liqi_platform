#!/usr/bin/env python3
"""Select and validate the predeclared application rollback target."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "operations" / "release-manifest-v0.schema.json"


def load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate(document: Any, path: Path) -> list[str]:
    schema = load(SCHEMA_PATH)
    return [
        f"{path}: {'.'.join(map(str, error.absolute_path))}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    current = load(args.current)
    failures = validate(current, args.current)
    rollback = current.get("rollback", {})
    target_id = rollback.get("previous_release_id")
    if not rollback.get("compatible"):
        failures.append("current release does not declare application rollback compatibility")
    if not target_id:
        failures.append("current release has no previous_release_id")

    target_path = args.history_dir / f"{target_id}.json" if target_id else args.history_dir / "missing.json"
    target: Any = None
    if target_id and not target_path.is_file():
        failures.append(f"rollback manifest not retained: {target_path}")
    elif target_id:
        target = load(target_path)
        failures.extend(validate(target, target_path))
        if target.get("release_id") != target_id:
            failures.append("rollback manifest release_id does not match requested target")
        if target.get("git_sha") == current.get("git_sha"):
            failures.append("rollback target must reference a different Git SHA")
        if target.get("deployment", {}).get("status") not in {"active", "rolled-back"}:
            failures.append("rollback target was never an active retained release")

    result = {
        "schema_version": "rollback-selection-v0",
        "status": "failed" if failures else "passed",
        "current_release_id": current.get("release_id"),
        "target_release_id": target_id,
        "target_manifest": str(target_path) if target_id else None,
        "deadline_seconds": rollback.get("deadline_seconds"),
        "database_rollback_allowed": False,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
