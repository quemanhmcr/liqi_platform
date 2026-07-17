#!/usr/bin/env python3
"""Run bounded liveness, readiness and platform-probe checks."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "operations" / "health-gate-target-v0.schema.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def safe_url(url: str, environment: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "health URL must not contain credentials, query parameters or fragments"
    if environment in {"staging", "production"} and parsed.scheme != "https":
        return "staging and production health URLs must use HTTPS"
    if parsed.scheme not in {"http", "https"}:
        return "health URL scheme must be http or https"
    return None


def perform(check: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any] | None]:
    request = urllib.request.Request(check["url"], headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=check["timeout_ms"] / 1000) as response:
            status = response.status
            body = response.read(65537)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"request failed: {type(exc).__name__}: {exc}", None
    if status != check["expected_status"]:
        return False, f"unexpected HTTP status {status}", None
    if len(body) > 65536:
        return False, "health response exceeded 65536 bytes", None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}", None
    for key, expected in check["expected_json"].items():
        if payload.get(key) != expected:
            return False, f"field {key!r} expected {expected!r}, got {payload.get(key)!r}", payload
    return True, None, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    target = load(args.target)
    schema = load(SCHEMA)
    schema_errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(target),
        key=lambda item: list(item.absolute_path),
    )
    if schema_errors:
        raise ValueError("; ".join(f"{'.'.join(map(str, error.absolute_path))}: {error.message}" for error in schema_errors))
    for check in target["checks"]:
        url_error = safe_url(check["url"], target["environment"])
        if url_error:
            raise ValueError(f"{check['name']}: {url_error}")

    started_at = utc_now()
    deadline = time.monotonic() + target["deadline_seconds"]
    attempts = 0
    latest: dict[str, dict[str, Any]] = {}
    passed = False
    while time.monotonic() < deadline:
        attempts += 1
        all_passed = True
        for check in target["checks"]:
            ok, error, payload = perform(check)
            latest[check["name"]] = {
                "owner": check["owner"],
                "kind": check["kind"],
                "status": "passed" if ok else "failed",
                "error": error,
                "observed_release_id": (payload.get("release_id") or payload.get("releaseId") or payload.get("version")) if payload else None,
            }
            all_passed = all_passed and ok
        if all_passed:
            passed = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(target["poll_interval_ms"] / 1000, remaining))

    result = {
        "schema_version": "health-gate-result-v0",
        "release_id": target["release_id"],
        "environment": target["environment"],
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": "passed" if passed else "failed",
        "attempts": attempts,
        "checks": [dict(name=name, **latest[name]) for name in sorted(latest)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
