#!/usr/bin/env python3
"""Shared exact-SHA/digest validation for project-owner build evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/operations/owner-build-evidence-v0.schema.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_ref(path: Path, root: Path = ROOT) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def resolve_repository_ref(reference: str, root: Path = ROOT) -> Path:
    candidate = (root / reference).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def validate_owner_evidence(
    record_path: Path,
    gate: dict[str, Any],
    git_sha: str,
    root: Path = ROOT,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not record_path.is_file():
        return None, [f"owner evidence missing at {record_path}"]
    try:
        record = load_json(record_path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"owner evidence is unreadable: {exc}"]
    schema = load_json(SCHEMA)
    failures = [
        f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record),
            key=lambda item: list(item.absolute_path),
        )
    ]
    if record.get("gate_id") != gate.get("id"):
        failures.append("owner evidence gate_id does not match provider gate")
    if record.get("git_sha") != git_sha:
        failures.append("owner evidence git_sha does not match current HEAD")
    if record.get("command") != gate.get("argv"):
        failures.append("owner evidence command does not exactly match provider registry argv")
    for ref_key, digest_key in (("log_ref", "log_sha256"), ("result_ref", "result_sha256")):
        reference = record.get(ref_key)
        if reference is None:
            continue
        try:
            artifact = resolve_repository_ref(str(reference), root)
        except (ValueError, OSError):
            failures.append(f"{ref_key} escapes repository")
            continue
        if not artifact.is_file():
            failures.append(f"{ref_key} does not exist: {reference}")
            continue
        if sha256(artifact) != record.get(digest_key):
            failures.append(f"{digest_key} does not match exact artifact bytes")
    if gate.get("result_mode") == "stdout-json":
        if not record.get("result_ref"):
            failures.append("stdout-json owner gate requires checksummed result_ref")
        else:
            try:
                load_json(resolve_repository_ref(record["result_ref"], root))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                failures.append(f"owner JSON result is invalid: {exc}")
    return record, failures
