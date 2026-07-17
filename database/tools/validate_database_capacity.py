#!/usr/bin/env python3
"""Validate the Senior 2 database capacity budget and optional Senior 4 schema."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
BUDGET_PATH = ROOT / "contracts/platform/database-capacity-budget-v0.json"
OPERATIONS_SCHEMA = ROOT / "contracts/operations/capacity-budget-v0.schema.json"
DATABASE_CONTRACT = ROOT / "contracts/platform/database-v0.example.json"

EXPECTED_COMPONENTS = {"postgresql-authority", "pgbouncer-boundary", "database-recovery"}
HARD_CEILING = {
    "ocpu": 1.2,
    "memory_mib": 7936,
    "disk_gib": 130.2,
    "postgres_connections": 50,
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    budget = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    contract = json.loads(DATABASE_CONTRACT.read_text(encoding="utf-8"))

    if OPERATIONS_SCHEMA.exists():
        schema = json.loads(OPERATIONS_SCHEMA.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(budget),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            fail("Senior 4 capacity schema rejected database provider: " + "; ".join(
                f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}" for error in errors
            ))

    if budget.get("schema_version") != "capacity-budget-v0":
        fail("unexpected capacity schema version")
    if budget.get("provider") != "database" or budget.get("owner") != "Senior 2":
        fail("capacity provider ownership must remain database/Senior 2")

    components = budget.get("components")
    if not isinstance(components, list):
        fail("capacity components must be an array")
    names = {component.get("name") for component in components}
    if names != EXPECTED_COMPONENTS:
        fail(f"capacity component set differs: {sorted(names)}")

    totals: dict[str, float] = {key: 0.0 for key in HARD_CEILING}
    for component in components:
        if component.get("default_enabled") is not True:
            fail(f"V0 database component is not default enabled: {component.get('name')}")
        steady = component["steady_state"]
        hard = component["hard_limit"]
        for key in ("ocpu", "memory_mib", "disk_gib"):
            if float(steady[key]) > float(hard[key]):
                fail(f"steady state exceeds hard limit for {component['name']} {key}")
            totals[key] += float(hard[key])
        totals["postgres_connections"] += int(component["postgres_connections"])
        if component["queue"].get("bounded") is not True:
            fail(f"unbounded queue declared by {component['name']}")
        if component["retry"]["maximum_attempts"] > 20:
            fail(f"retry exceeds V0 bound for {component['name']}")

    for key, ceiling in HARD_CEILING.items():
        if totals[key] > ceiling + 1e-9:
            fail(f"database hard capacity exceeded for {key}: {totals[key]} > {ceiling}")

    contract_capacity = contract["capacity"]
    if contract_capacity["hardMemoryMiB"] != int(HARD_CEILING["memory_mib"]):
        fail("database contract hard memory differs from provider capacity")
    if contract_capacity["diskBudgetGiB"] < math.ceil(HARD_CEILING["disk_gib"]):
        fail("database contract disk budget is smaller than provider hard disk total")
    if contract["connectionBudget"]["postgresMaxConnections"] != 80:
        fail("PostgreSQL max_connections contract unexpectedly changed")
    if (
        contract["connectionBudget"]["runtimeServerConnections"]
        + contract["connectionBudget"]["operationalPoolConnections"]
        + contract["connectionBudget"]["directAdministrativeConnections"]
        + contract["connectionBudget"]["reservedHeadroom"]
        != 80
    ):
        fail("PostgreSQL connection partition no longer equals max_connections")
    components_by_name = {component["name"]: component for component in components}
    pooled_capacity = int(components_by_name["pgbouncer-boundary"]["postgres_connections"])
    pooled_demand = (
        contract["connectionBudget"]["runtimeServerConnections"]
        + contract["connectionBudget"]["operationalPoolConnections"]
    )
    direct_capacity = sum(
        int(component["postgres_connections"])
        for component in components
        if component["name"] != "pgbouncer-boundary"
    )
    if pooled_capacity != pooled_demand:
        fail(f"PgBouncer server capacity differs from pooled demand: {pooled_capacity} != {pooled_demand}")
    if direct_capacity != contract["connectionBudget"]["directAdministrativeConnections"]:
        fail(
            "direct database provider reservation differs from contract: "
            f"{direct_capacity} != {contract['connectionBudget']['directAdministrativeConnections']}"
        )
    if int(totals["postgres_connections"]) != pooled_capacity + direct_capacity:
        fail("database server reservation does not equal pooled plus direct capacity")

    rendered_totals = {
        "ocpu": round(totals["ocpu"], 3),
        "memoryMiB": int(totals["memory_mib"]),
        "diskGiB": round(totals["disk_gib"], 3),
        "postgresConnections": int(totals["postgres_connections"]),
    }
    print(json.dumps({
        "validation": "database-capacity-budget-v0",
        "components": sorted(EXPECTED_COMPONENTS),
        "hardTotals": rendered_totals,
        "senior4SchemaApplied": OPERATIONS_SCHEMA.exists(),
        "passed": True,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
