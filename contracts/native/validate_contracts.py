#!/usr/bin/env python3
"""Validate the six Senior 3 native schemas and published source-ready documents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "native"
EXAMPLE_DIR = CONTRACT_DIR / "examples"

PAIRS = (
    ("native-capabilities-v1.schema.json", "native-capabilities-v1.example.json"),
    ("native-kernel-v1.schema.json", "native-kernel-v1.example.json"),
    ("native-error-v1.schema.json", "native-error-v1.example.json"),
    ("native-artifact-v1.schema.json", "native-artifact-v1.example.json"),
    ("native-benchmark-v1.schema.json", "native-benchmark-v1.example.json"),
    ("rust-port-protocol-v1.schema.json", "rust-port-protocol-v1.example.json"),
)

LIVE_DOCUMENTS = (
    ("native-capabilities-v1.schema.json", CONTRACT_DIR / "native-capabilities-v1.json"),
    ("native-kernel-v1.schema.json", CONTRACT_DIR / "compact-sequence-diff-v1.json"),
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_registry() -> Registry:
    resources: list[tuple[str, Resource[Any]]] = []
    for path in CONTRACT_DIR.glob("*.schema.json"):
        schema = load_json(path)
        resource = Resource.from_contents(schema)
        resources.append((path.resolve().as_uri(), resource))
        if identifier := schema.get("$id"):
            resources.append((str(identifier), resource))
    return Registry().with_resources(resources)


def validate_document(schema_path: Path, document_path: Path, registry: Registry) -> list[str]:
    try:
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        document = load_json(document_path)
    except (OSError, json.JSONDecodeError, SchemaError) as error:
        return [f"{document_path.relative_to(ROOT)}: {error}"]

    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    return [
        f"{document_path.relative_to(ROOT)} at "
        f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    ]


def validate_all(quiet: bool = False) -> list[str]:
    failures: list[str] = []
    registry = schema_registry()
    for schema_name, fixture_name in PAIRS:
        schema_path = CONTRACT_DIR / schema_name
        fixture_path = EXAMPLE_DIR / fixture_name
        errors = validate_document(schema_path, fixture_path, registry)
        failures.extend(errors)
        if not quiet and not errors:
            print(f"PASS {schema_path.relative_to(ROOT)} <- {fixture_path.relative_to(ROOT)}")
    for schema_name, document_path in LIVE_DOCUMENTS:
        errors = validate_document(CONTRACT_DIR / schema_name, document_path, registry)
        failures.extend(errors)
        if not quiet and not errors:
            print(f"PASS {schema_name} <- {document_path.relative_to(ROOT)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    failures = validate_all(args.quiet)
    if failures:
        for failure in failures:
            print(f"FAIL native-contract: {failure}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            json.dumps(
                {
                    "validation": "native-contracts-v1",
                    "status": "passed",
                    "schemas": len(PAIRS),
                    "live_documents": len(LIVE_DOCUMENTS),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
