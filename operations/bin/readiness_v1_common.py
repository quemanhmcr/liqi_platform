#!/usr/bin/env python3
"""Shared primitives for the LIQI V1 readiness control plane.

This module validates and composes provider evidence. It deliberately contains
no runtime, database, native, infrastructure, deployment, or recovery logic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
STATUS_ORDER = {"passed": 0, "blocked": 1, "failed": 2}
SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)(password|secret|token|private[_-]?key)(\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("date-time must include an offset")
    return parsed.astimezone(timezone.utc)


def age_seconds(value: str, *, now: datetime | None = None) -> int:
    reference = now or datetime.now(timezone.utc)
    return max(0, int((reference - parse_datetime(value)).total_seconds()))


def validate_document(schema_path: Path, document: Any, label: str) -> list[str]:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    formatted: list[str] = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        formatted.append(f"{label}.{location}: {error.message}")
    return formatted


def combine_status(statuses: Iterable[str]) -> str:
    result = "passed"
    for status in statuses:
        if STATUS_ORDER.get(status, 2) > STATUS_ORDER[result]:
            result = status if status in STATUS_ORDER else "failed"
    return result


def safe_repo_path(relative: str) -> Path:
    candidate = (ROOT / relative).resolve()
    candidate.relative_to(ROOT.resolve())
    return candidate


def relative_ref(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if "PRIVATE KEY" in pattern.pattern:
            redacted = pattern.sub("<redacted-private-key>", redacted)
        else:
            redacted = pattern.sub(lambda match: "".join(part or "" for part in match.groups()) + "<redacted>", redacted)
    for name, value in os.environ.items():
        if value and any(token in name.upper() for token in ("SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY", "CREDENTIAL")):
            redacted = redacted.replace(value, f"<redacted:{name}>")
    return redacted


def blocker(owner: str, seam: str, code: str, severity: str, message: str, action_required: str) -> dict[str, str]:
    return {
        "owner": owner,
        "seam": seam,
        "code": code,
        "severity": severity,
        "message": message[:1000],
        "action_required": action_required[:1000],
    }


def exact_set(values: Iterable[str], expected: set[str], label: str) -> list[str]:
    materialized = list(values)
    failures: list[str] = []
    duplicates = sorted({value for value in materialized if materialized.count(value) > 1})
    if duplicates:
        failures.append(f"{label} contains duplicates: {duplicates}")
    actual = set(materialized)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        failures.append(f"{label} is missing: {missing}")
    if extra:
        failures.append(f"{label} contains unexpected values: {extra}")
    return failures
