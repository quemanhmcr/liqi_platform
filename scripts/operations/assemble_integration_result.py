#!/usr/bin/env python3
"""Compose provider, capacity, recovery and platform-probe evidence for promotion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
RESULT_SCHEMA = ROOT / "contracts" / "operations" / "integration-result-v0.schema.json"
PROBE_SCHEMA = ROOT / "contracts" / "operations" / "platform-probe-result-v0.schema.json"
CAPACITY_SCHEMA = ROOT / "contracts" / "operations" / "capacity-result-v0.schema.json"
RECOVERY_SCHEMA = ROOT / "contracts" / "operations" / "recovery-freshness-result-v0.schema.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(schema_path: Path, document: Any, label: str) -> list[str]:
    schema = load(schema_path)
    return [
        f"{label}.{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def gate(seam: str, command: str, status: str, output_ref: str, failure_class: str | None) -> dict[str, Any]:
    return {
        "owner": "Senior 4",
        "seam": seam,
        "command": command,
        "status": status,
        "exit_code": 0 if status == "passed" else 1,
        "duration_ms": 0,
        "output_ref": output_ref,
        "failure_class": failure_class,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-result", type=Path, required=True)
    parser.add_argument("--capacity-result", type=Path, required=True)
    parser.add_argument("--recovery-result", type=Path, required=True)
    parser.add_argument("--platform-probe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    provider = load(args.provider_result)
    capacity = load(args.capacity_result)
    recovery = load(args.recovery_result)
    probe = load(args.platform_probe)
    failures: list[str] = []
    failures.extend(validate(RESULT_SCHEMA, provider, "provider_result"))
    failures.extend(validate(CAPACITY_SCHEMA, capacity, "capacity_result"))
    failures.extend(validate(RECOVERY_SCHEMA, recovery, "recovery_result"))
    failures.extend(validate(PROBE_SCHEMA, probe, "platform_probe"))

    if provider.get("mode") != "provider":
        failures.append("provider result must use provider mode; mock evidence cannot promote")
    if provider.get("overall_status") != "passed":
        failures.append(f"provider result is not passed: {provider.get('overall_status')}")
    if capacity.get("status") != "passed":
        failures.append("capacity result is not passed")
    if provider.get("environment") in {"staging", "production"} and recovery.get("status") != "passed":
        failures.append("staging/production promotion requires passed recovery freshness")
    if probe.get("status") != "passed":
        failures.append("platform probe is not passed")
    if probe.get("release_id") != provider.get("release_id") or probe.get("observed_release_id") != provider.get("release_id"):
        failures.append("platform probe release ID does not match integration release")
    if probe.get("environment") != provider.get("environment"):
        failures.append("platform probe environment does not match integration environment")
    if any(check.get("status") != "passed" for check in probe.get("checks", [])):
        failures.append("platform probe contains a failed check")

    status = "failed" if failures else "passed"
    violations = list(provider.get("violations", []))
    for message in failures:
        violations.append({
            "owner": "Senior 4",
            "seam": "promotion evidence composition",
            "code": "PROMOTION_EVIDENCE_INVALID",
            "message": message,
            "action_required": "Correct the owning provider evidence; do not add a Senior 4 fallback or mock.",
        })

    envelope = capacity.get("envelope", {})
    totals = capacity.get("totals", {})
    result = {
        **provider,
        "overall_status": status,
        "gates": list(provider.get("gates", [])) + [
            gate("capacity aggregation", "scripts/operations/check_capacity.py", "passed" if capacity.get("status") == "passed" else "failed", args.capacity_result.as_posix(), None if capacity.get("status") == "passed" else "capacity"),
            gate("recovery freshness", "scripts/operations/check_recovery_freshness.py", "passed" if recovery.get("status") == "passed" else "failed", args.recovery_result.as_posix(), None if recovery.get("status") == "passed" else "recovery"),
            gate("platform probe evaluation", "provider-owned platform probe", "passed" if probe.get("status") == "passed" else "failed", args.platform_probe.as_posix(), None if probe.get("status") == "passed" else "runtime"),
            gate("promotion evidence compatibility", "scripts/operations/assemble_integration_result.py", "failed" if failures else "passed", args.output.as_posix(), "contract" if failures else None),
        ],
        "capacity": {
            "status": "passed" if capacity.get("status") == "passed" else "failed",
            "hard_limit_ocpu": envelope.get("host", {}).get("ocpu", 4),
            "hard_limit_memory_mib": envelope.get("host", {}).get("memory_mib", 24576),
            "reserved_ocpu": envelope.get("reserved", {}).get("ocpu", 1),
            "reserved_memory_mib": envelope.get("reserved", {}).get("memory_mib", 4096),
            "declared_ocpu": totals.get("ocpu", 0),
            "declared_memory_mib": totals.get("memory_mib", 0),
            "disk_budget_gib": totals.get("disk_gib", 0),
        },
        "recovery": {
            "status": "passed" if recovery.get("status") == "passed" else "failed",
            "backup_age_seconds": recovery.get("backup_age_seconds"),
            "wal_archive_lag_seconds": recovery.get("wal_archive_lag_seconds"),
            "restore_verified_at": recovery.get("restore_verified_at"),
            "evidence_ref": recovery.get("evidence_ref"),
        },
        "platform_probe": {
            "status": "passed" if probe.get("status") == "passed" else "failed",
            "result_ref": args.platform_probe.as_posix(),
            "release_id_observed": probe.get("observed_release_id"),
        },
        "violations": violations,
    }
    output_errors = validate(RESULT_SCHEMA, result, "integration_result")
    if output_errors:
        for error in output_errors:
            print(f"ERROR assemble-integration: {error}", file=sys.stderr)
        return 65
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"assembled integration result {status}: {args.output}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
