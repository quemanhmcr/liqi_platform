#!/usr/bin/env python3
"""Validate Senior 3 runtime operability declarations against shared schemas.

This is a consumer-side contract validator. It does not compile Rust, inspect
runtime behavior, or substitute provider implementation tests.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CAPACITY_SCHEMA = ROOT / "contracts/operations/capacity-budget-v0.schema.json"
TELEMETRY_SCHEMA = ROOT / "contracts/operations/telemetry-v0.schema.json"
EXPECTED_COMPONENTS = {"liqi-api", "liqi-realtime", "liqi-worker"}
EXPECTED_TELEMETRY = {
    "liqi-api": "contracts/platform/runtime-telemetry-api-v0.json",
    "liqi-realtime": "contracts/platform/runtime-telemetry-realtime-v0.json",
    "liqi-worker": "contracts/platform/runtime-telemetry-worker-v0.json",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(schema: dict[str, Any], document: Any, label: str) -> list[str]:
    return [
        f"{label}: {'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-root", type=Path, default=ROOT)
    args = parser.parse_args()
    provider = args.provider_root.resolve()
    failures: list[str] = []

    capacity_path = provider / "contracts/platform/runtime-capacity-budget-v0.json"
    if not capacity_path.is_file():
        failures.append("runtime capacity declaration is missing")
    else:
        capacity = load(capacity_path)
        failures.extend(schema_errors(load(CAPACITY_SCHEMA), capacity, capacity_path.name))
        if capacity.get("provider") != "runtime" or capacity.get("owner") != "Senior 3":
            failures.append("runtime capacity declaration must be provider=runtime and owner=Senior 3")
        names = [item.get("name") for item in capacity.get("components", [])]
        if set(names) != EXPECTED_COMPONENTS or len(names) != len(EXPECTED_COMPONENTS):
            failures.append("runtime capacity must declare liqi-api, liqi-realtime and liqi-worker exactly once")
        for component in capacity.get("components", []):
            steady = component.get("steady_state", {})
            hard = component.get("hard_limit", {})
            for resource in ("ocpu", "memory_mib", "disk_gib"):
                if float(hard.get(resource, 0)) < float(steady.get(resource, 0)):
                    failures.append(f"{component.get('name')}: hard {resource} is below steady-state")

    telemetry_schema = load(TELEMETRY_SCHEMA)
    for service, relative in EXPECTED_TELEMETRY.items():
        path = provider / relative
        if not path.is_file():
            failures.append(f"missing telemetry declaration: {relative}")
            continue
        document = load(path)
        failures.extend(schema_errors(telemetry_schema, document, path.name))
        declared = document.get("service", {})
        if declared.get("name") != service or declared.get("owner") != "Senior 3":
            failures.append(f"{path.name}: service identity/owner mismatch")

    if failures:
        for failure in failures:
            print(f"ERROR runtime-operability-contract: {failure}", file=sys.stderr)
        return 1
    print("validated Senior 3 runtime capacity and three telemetry declarations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
