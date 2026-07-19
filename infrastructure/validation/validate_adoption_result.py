#!/usr/bin/env python3
"""Validate that state adoption passed for the exact temporary-E5 source revision."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/infrastructure/adoption-result-v1.schema.json"


def validate_result(document: dict[str, object], git_sha: str) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise ValueError(f"invalid adoption result: {errors[0].message}")
    if document.get("git_sha") != git_sha or document.get("capacity_profile") != "e5-temporary":
        raise ValueError("adoption result source/profile mismatch")
    if document.get("operation") != "execute" or document.get("status") != "passed" or document.get("blockers"):
        raise ValueError("adoption result is not an executed pass")
    if document.get("oci_mutation_performed") is not False:
        raise ValueError("adoption result incorrectly claims OCI mutation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--git-sha", required=True)
    args = parser.parse_args()
    try:
        validate_result(json.loads(args.result.read_text(encoding="utf-8")), args.git_sha)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print("validated exact-SHA E5 state adoption result")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
