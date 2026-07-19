#!/usr/bin/env python3
"""Validate an exact-SHA passed E5 pre-apply readiness result and its plan inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/infrastructure/pre-apply-readiness-v1.schema.json"
CHECKS = {
    "oci-adoption-handoff",
    "state-backend",
    "state-adoption",
    "protected-tfvars",
    "signed-x86-release",
    "rollback-target",
    "protected-environment",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    return path.resolve()


def load(path: Path, label: str) -> dict[str, Any]:
    document = json.loads(regular(path, label).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{label} JSON root must be an object")
    return document


def validate_document(document: dict[str, Any], git_sha: str) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise ValueError(f"invalid pre-apply readiness result: {errors[0].message}")
    if document.get("git_sha") != git_sha or document.get("capacity_profile") != "e5-temporary":
        raise ValueError("pre-apply readiness source/profile mismatch")
    if document.get("status") != "passed" or document.get("blockers"):
        raise ValueError("pre-apply readiness is not passed")
    if document.get("state_mutation_performed") is not False or document.get("oci_mutation_performed") is not False:
        raise ValueError("pre-apply readiness incorrectly claims mutation")
    checks = document.get("checks", [])
    if {item.get("name") for item in checks} != CHECKS or any(item.get("status") != "passed" for item in checks):
        raise ValueError("pre-apply readiness does not contain the exact seven passed checks")


def validate_result(
    document: dict[str, Any],
    git_sha: str,
    state_backend_evidence: Path,
    adoption_result_path: Path,
    var_file: Path,
) -> None:
    validate_document(document, git_sha)
    state_backend_evidence = regular(state_backend_evidence, "state-backend evidence")
    adoption_result_path = regular(adoption_result_path, "adoption result")
    var_file = regular(var_file, "protected tfvars")
    adoption_result = load(adoption_result_path, "adoption result")
    inputs = document.get("inputs", {})
    expected = {
        "state_backend_evidence_sha256": digest(state_backend_evidence),
        "adoption_result_sha256": digest(adoption_result_path),
        "var_file_sha256": digest(var_file),
        "adoption_manifest_sha256": adoption_result.get("manifest_sha256"),
    }
    for name, value in expected.items():
        if not isinstance(value, str) or inputs.get(name) != value:
            raise ValueError(f"pre-apply readiness input digest mismatch: {name}")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--state-backend-evidence", required=True, type=Path)
    parser.add_argument("--adoption-result", required=True, type=Path)
    parser.add_argument("--var-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        result_path = regular(args.result, "pre-apply readiness result")
        validate_result(
            load(result_path, "pre-apply readiness result"),
            args.git_sha,
            args.state_backend_evidence,
            args.adoption_result,
            args.var_file,
        )
    except (ValueError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    print("validated exact-SHA passed E5 pre-apply readiness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
