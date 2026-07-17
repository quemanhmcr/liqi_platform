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
RESOURCE_KEYS = ("ocpu", "memory_mib", "disk_gib")


def load(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def empty_totals() -> dict[str, Any]:
    return {"ocpu": 0.0, "memory_mib": 0, "disk_gib": 0.0, "postgres_connections": 0}


def rounded(totals: dict[str, Any]) -> dict[str, Any]:
    totals["ocpu"] = round(float(totals["ocpu"]), 6)
    totals["disk_gib"] = round(float(totals["disk_gib"]), 6)
    return totals


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
    steady_totals = empty_totals()
    hard_totals = empty_totals()
    components: list[dict[str, Any]] = []
    server_reservation = 0
    pooled_server_capacity = 0
    direct_reserved_capacity = 0
    pooled_runtime_demand = 0

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
            steady = component["steady_state"]
            hard = component["hard_limit"]
            for key in RESOURCE_KEYS:
                if float(hard[key]) < float(steady[key]):
                    failures.append(f"{provider}/{component['name']}: hard {key} is below steady-state")
                steady_totals[key] += float(steady[key]) if key != "memory_mib" else int(steady[key])
                hard_totals[key] += float(hard[key]) if key != "memory_mib" else int(hard[key])
            connections = int(component["postgres_connections"])
            if provider == "database":
                server_reservation += connections
                if component.get("class") == "pooler":
                    pooled_server_capacity += connections
                else:
                    direct_reserved_capacity += connections
            else:
                pooled_runtime_demand += connections
            components.append({
                "provider": provider,
                "name": component["name"],
                "steady_state": {key: steady[key] for key in RESOURCE_KEYS},
                "hard_limit": {key: hard[key] for key in RESOURCE_KEYS},
                # Deprecated flat aliases retain V0 readers that interpreted component resources as hard ceilings.
                "ocpu": hard["ocpu"],
                "memory_mib": hard["memory_mib"],
                "disk_gib": hard["disk_gib"],
                "postgres_connections": connections,
            })

    steady_totals["postgres_connections"] = pooled_runtime_demand
    hard_totals["postgres_connections"] = server_reservation
    steady_totals = rounded(steady_totals)
    hard_totals = rounded(hard_totals)

    missing = REQUIRED_PROVIDERS - providers
    if missing:
        failures.append(f"missing provider budgets: {', '.join(sorted(missing))}")

    steady_limits = envelope["steady_state_limit"]
    for key in RESOURCE_KEYS:
        if steady_totals[key] > steady_limits[key]:
            failures.append(f"steady-state capacity exceeded for {key}: declared={steady_totals[key]} limit={steady_limits[key]}")

    hard_limits = envelope["provider_hard_limit"]
    for key in RESOURCE_KEYS:
        if hard_totals[key] > hard_limits[key]:
            failures.append(f"hard ceiling exceeded for {key}: declared={hard_totals[key]} limit={hard_limits[key]}")
    if server_reservation > hard_limits["postgres_connections"]:
        failures.append(
            f"PostgreSQL server reservation exceeded: declared={server_reservation} limit={hard_limits['postgres_connections']}"
        )
    if pooled_runtime_demand > pooled_server_capacity:
        failures.append(
            f"pooled runtime demand exceeds PgBouncer server capacity: demand={pooled_runtime_demand} capacity={pooled_server_capacity}"
        )

    connection_accounting = {
        "server_reservation": server_reservation,
        "pooled_server_capacity": pooled_server_capacity,
        "pooled_runtime_demand": pooled_runtime_demand,
        "direct_reserved_capacity": direct_reserved_capacity,
    }
    report = {
        "schema_version": "capacity-result-v0",
        "status": "failed" if failures else "passed",
        "envelope": envelope,
        # Legacy totals remain the hard-ceiling aggregate.
        "totals": hard_totals,
        "steady_state_totals": steady_totals,
        "hard_limit_totals": hard_totals,
        "postgres_connection_accounting": connection_accounting,
        "components": sorted(components, key=lambda item: (str(item["provider"]), str(item["name"]))),
        "failures": failures,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    print(encoded, end="")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
