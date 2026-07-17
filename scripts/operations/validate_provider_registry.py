#!/usr/bin/env python3
"""Validate provider gate registry shape and Senior 4 safety invariants."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "operations" / "provider-gates-v0.schema.json"
DEFAULT_REGISTRY = ROOT / "operations" / "integration" / "provider-gates-v0.json"
ENV_PLACEHOLDER = re.compile(r"\{env:([A-Z][A-Z0-9_]{1,63})\}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(registry_path: Path, allow_pending: bool) -> list[str]:
    schema = load_json(SCHEMA_PATH)
    registry = load_json(registry_path)
    errors = [
        f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(Draft202012Validator(schema).iter_errors(registry), key=lambda item: list(item.absolute_path))
    ]
    gates = registry.get("gates", []) if isinstance(registry, dict) else []
    ids = [gate.get("id") for gate in gates if isinstance(gate, dict)]
    if len(ids) != len(set(ids)):
        errors.append("gate IDs must be unique")
    owners = {gate.get("owner") for gate in gates if isinstance(gate, dict)}
    if owners != {"Senior 1", "Senior 2", "Senior 3"}:
        errors.append("registry must contain gates for Senior 1, Senior 2 and Senior 3")
    if any(gate.get("mutation_class") in {"host-mutation", "oci-mutation"} for gate in gates if isinstance(gate, dict)):
        errors.append("V0 provider validation registry must not contain host or OCI mutation commands")
    if not allow_pending:
        pending = [gate.get("id") for gate in gates if gate.get("provider_state") in {"pending-provider", "pending-owner-build"}]
        if pending:
            errors.append(f"pending provider gates are forbidden in strict mode: {pending}")
    for gate in gates:
        if gate.get("result_mode") == "json-file" and "{output}" not in gate.get("argv", []):
            errors.append(f"{gate.get('id')}: json-file gate must receive an explicit {{output}} argument")
        if gate.get("provider_state") == "deprecated" and "promotion" in gate.get("stages", []):
            errors.append(f"{gate.get('id')}: deprecated gate cannot participate in promotion")
        placeholders = {name for part in gate.get("argv", []) for name in ENV_PLACEHOLDER.findall(part)}
        declared = set(gate.get("required_environment", []))
        if placeholders - declared:
            errors.append(f"{gate.get('id')}: env placeholders must be declared in required_environment: {sorted(placeholders - declared)}")
        if declared - placeholders and gate.get("id") == "oci-plan-read-only":
            errors.append(f"{gate.get('id')}: OCI plan environment must be consumed through a secret-safe env placeholder")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()
    failures = validate(args.registry.resolve(), args.allow_pending)
    if failures:
        for failure in failures:
            print(f"ERROR provider-registry: {failure}", file=sys.stderr)
        return 1
    print(f"validated provider registry: {args.registry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
