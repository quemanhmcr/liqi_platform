#!/usr/bin/env python3
"""Validate Senior 4 JSON Schemas and their canonical fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "operations"
FIXTURE_DIR = ROOT / "tests" / "contract" / "fixtures" / "operations"
PAIRS = (
    ("release-manifest-v0.schema.json", "release-manifest.valid.json"),
    ("telemetry-v0.schema.json", "telemetry.valid.json"),
    ("integration-result-v0.schema.json", "integration-result.valid.json"),
    ("capacity-budget-v0.schema.json", "../capacity/infrastructure.valid.json"),
    ("health-gate-target-v0.schema.json", "../../../integration/fixtures/health-gate-target.valid.json"),
    ("recovery-status-v0.schema.json", "../../../integration/fixtures/recovery-status.valid.json"),
    ("slo-catalog-v0.schema.json", "../../../../operations/slo/slo-catalog-v0.json"),
    ("provider-gates-v0.schema.json", "../../../../operations/integration/provider-gates-v0.json"),
    ("dependency-policy-v0.schema.json", "../../../../operations/release/dependency-policy-v0.json"),
    ("capacity-result-v0.schema.json", "capacity-result.valid.json"),
    ("recovery-freshness-result-v0.schema.json", "recovery-freshness-result.valid.json"),
    ("platform-probe-result-v0.schema.json", "../../../integration/fixtures/platform-probe-result.valid.json"),
    ("deployment-spec-v0.schema.json", "deployment-spec.valid.json"),
    ("activation-result-v0.schema.json", "activation-result.valid.json"),
    ("telemetry-runtime-policy-v0.schema.json", "../../../../operations/telemetry/telemetry-runtime-policy-v0.json"),
    ("telemetry-sink-v0.schema.json", "../../../../operations/telemetry/telemetry-sink-v0.example.json"),
    ("recovery-exercise-plan-v0.schema.json", "../../../../operations/disaster-recovery/recovery-exercise-plan-v0.example.json"),
    ("recovery-exercise-result-v0.schema.json", "recovery-exercise-result.valid.json"),
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def format_error(path: Path, error: Exception) -> str:
    location = ".".join(str(part) for part in getattr(error, "absolute_path", ()))
    suffix = f" at {location}" if location else ""
    return f"{path.as_posix()}{suffix}: {error.message if hasattr(error, 'message') else error}"


def validate_semantics(fixture_name: str, fixture: Any) -> list[str]:
    errors: list[str] = []
    if fixture_name == "release-manifest.valid.json":
        names = [artifact["name"] for artifact in fixture.get("artifacts", [])]
        expected = {"liqi-api", "liqi-realtime", "liqi-worker"}
        if set(names) != expected or len(names) != len(expected):
            errors.append("release artifacts must contain each of liqi-api, liqi-realtime and liqi-worker exactly once")
        if fixture.get("infrastructure", {}).get("cost_classification") in {"paid", "unknown"}:
            status = fixture.get("deployment", {}).get("status")
            approved_by = fixture.get("deployment", {}).get("approved_by")
            if status in {"activating", "active"} and not approved_by:
                errors.append("paid or unknown infrastructure cannot activate without explicit approval")
    elif fixture_name == "telemetry.valid.json":
        forbidden = set(fixture.get("cardinality_policy", {}).get("forbidden_labels", []))
        for metric in fixture.get("metrics", []):
            overlap = forbidden.intersection(metric.get("labels", []))
            if overlap:
                errors.append(f"metric {metric.get('name')} uses forbidden labels: {sorted(overlap)}")
    elif fixture_name == "capacity-result.valid.json":
        if fixture.get("status") == "passed" and fixture.get("failures"):
            errors.append("passed capacity result cannot contain failures")
        providers = {item.get("provider") for item in fixture.get("components", [])}
        if providers != {"infrastructure", "database", "runtime", "operations"}:
            errors.append("capacity result must aggregate all four provider budgets")
    elif fixture_name == "recovery-freshness-result.valid.json":
        if fixture.get("status") == "passed" and fixture.get("failures"):
            errors.append("passed recovery freshness result cannot contain failures")
    elif fixture_name == "platform-probe-result.valid.json":
        if fixture.get("status") == "passed":
            if fixture.get("release_id") != fixture.get("observed_release_id"):
                errors.append("passed platform probe must observe the requested release ID")
            if fixture.get("errors"):
                errors.append("passed platform probe cannot contain errors")
            if any(item.get("status") != "passed" for item in fixture.get("checks", [])):
                errors.append("passed platform probe cannot contain failed checks")
    elif fixture_name == "deployment-spec.valid.json":
        artifact_names = [item.get("name") for item in fixture.get("artifacts", [])]
        service_names = [item.get("name") for item in fixture.get("services", [])]
        expected = {"liqi-api", "liqi-realtime", "liqi-worker"}
        if set(artifact_names) != expected or len(artifact_names) != 3:
            errors.append("deployment spec must install each runtime artifact exactly once")
        if set(service_names) != expected or len(service_names) != 3:
            errors.append("deployment spec must control each runtime service exactly once")
    elif fixture_name == "integration-result.valid.json":
        statuses = [item.get("status") for item in fixture.get("provider_results", []) + fixture.get("gates", [])]
        expected_overall = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
        if fixture.get("overall_status") != expected_overall:
            errors.append(f"overall_status must be {expected_overall} for contained check results")
        if fixture.get("mode") == "provider" and any(item.get("command", "").startswith("mock:") for item in fixture.get("provider_results", [])):
            errors.append("provider mode cannot contain mock provider commands")
    return errors


def validate_pair(schema_path: Path, fixture_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        return [format_error(schema_path, exc)]

    try:
        fixture = load_json(fixture_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [format_error(fixture_path, exc)]

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for validation_error in sorted(validator.iter_errors(fixture), key=lambda item: list(item.absolute_path)):
        errors.append(format_error(fixture_path, validation_error))
    errors.extend(f"{fixture_path.as_posix()}: {message}" for message in validate_semantics(fixture_path.name, fixture))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    for schema_name, fixture_name in PAIRS:
        schema_path = CONTRACT_DIR / schema_name
        fixture_path = FIXTURE_DIR / fixture_name
        pair_errors = validate_pair(schema_path, fixture_path)
        failures.extend(pair_errors)
        if not args.quiet and not pair_errors:
            print(f"PASS {schema_path.relative_to(ROOT)} <- {fixture_path.relative_to(ROOT)}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Validated {len(PAIRS)} operations contracts with Draft 2020-12.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
