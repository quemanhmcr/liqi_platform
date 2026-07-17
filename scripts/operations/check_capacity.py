#!/usr/bin/env python3
"""Validate and aggregate provider capacity budgets for the V0 single node."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = ROOT / "contracts" / "operations" / "capacity-budget-v0.schema.json"
DEFAULT_ENVELOPE = ROOT / "operations" / "capacity" / "capacity-envelope-v0.json"
REQUIRED_PROVIDERS = {"infrastructure", "database", "runtime", "operations"}


def load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("budgets", nargs="+", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--envelope", type=Path, default=DEFAULT_ENVELOPE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    schema = load(args.schema)
    envelope = load(args.envelope)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    failures: list[str] = []
    providers: set[str] = set()
    totals = {"ocpu": 0.0, "memory_mib": 0, "disk_gib": 0.0, "postgres_connections": 0}
    components: list[dict[str, Any]] = []

    for path in args.budgets:
        budget = load(path)
        errors = sorted(validator.iter_errors(budget), key=lambda item: list(item.absolute_path))
        for error in errors:
            failures.append(f"{path}: {'.'.join(map(str, error.absolute_path))}: {error.message}")
        provider = budget.get("provider")
        if provider in providers:
            failures.append(f"duplicate provider budget: {provider}")
        providers.add(provider)
        for component in budget.get("components", []):
            if not component.get("default_enabled"):
                continue
            hard = component["hard_limit"]
            totals["ocpu"] += float(hard["ocpu"])
            totals["memory_mib"] += int(hard["memory_mib"])
            totals["disk_gib"] += float(hard["disk_gib"])
            totals["postgres_connections"] += int(component["postgres_connections"])
            components.append({
                "provider": provider,
                "name": component["name"],
                "ocpu": hard["ocpu"],
                "memory_mib": hard["memory_mib"],
                "disk_gib": hard["disk_gib"],
                "postgres_connections": component["postgres_connections"]
            })

    totals["ocpu"] = round(float(totals["ocpu"]), 6)
    totals["disk_gib"] = round(float(totals["disk_gib"]), 6)

    missing = REQUIRED_PROVIDERS - providers
    if missing:
        failures.append(f"missing provider budgets: {', '.join(sorted(missing))}")

    limits = envelope["provider_hard_limit"]
    for key in ("ocpu", "memory_mib", "disk_gib", "postgres_connections"):
        if totals[key] > limits[key]:
            failures.append(f"capacity exceeded for {key}: declared={totals[key]} limit={limits[key]}")

    report = {
        "schema_version": "capacity-result-v0",
        "status": "failed" if failures else "passed",
        "envelope": envelope,
        "totals": totals,
        "components": sorted(components, key=lambda item: (str(item["provider"]), str(item["name"]))),
        "failures": failures
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    print(encoded, end="")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
