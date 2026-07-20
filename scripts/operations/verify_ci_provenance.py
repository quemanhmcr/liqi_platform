#!/usr/bin/env python3
"""Fail closed unless checkout, event source SHA, and release ID are identical."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def event_source_sha(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name == "push":
        value = payload.get("after")
    elif event_name == "pull_request":
        value = payload.get("pull_request", {}).get("head", {}).get("sha")
    else:
        value = None
    return value if isinstance(value, str) else None


def evaluate(
    *,
    actual_sha: str,
    expected_sha: str,
    github_sha: str,
    event_name: str,
    payload: dict[str, Any],
    release_id: str,
) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    derived_event_sha = event_source_sha(event_name, payload)
    release_suffix = release_id.removeprefix("liqi-v1-ci-")
    for name, value in (
        ("actual checkout SHA", actual_sha),
        ("expected source SHA", expected_sha),
        ("GitHub SHA", github_sha),
        ("event source SHA", derived_event_sha or ""),
        ("release ID suffix", release_suffix),
    ):
        if not SHA.fullmatch(value):
            failures.append(f"{name} is not an exact lowercase Git SHA")
    if actual_sha != expected_sha:
        failures.append("checked-out SHA differs from expected source SHA")
    if derived_event_sha != expected_sha:
        failures.append("GitHub event source SHA differs from expected source SHA")
    if release_suffix != expected_sha:
        failures.append("release ID suffix differs from expected source SHA")

    github_sha_kind = "source"
    if event_name == "push":
        if github_sha != expected_sha:
            failures.append("push github.sha differs from event after/source SHA")
    elif event_name == "pull_request":
        # GitHub may regenerate refs/pull/<n>/merge after the webhook payload is
        # captured.  In that case GITHUB_SHA is a valid synthetic merge SHA but
        # pull_request.merge_commit_sha is stale.  It is provenance metadata,
        # never the source authority: checkout, event head, expected SHA and
        # release suffix are all required above to match exactly.
        if github_sha != expected_sha:
            github_sha_kind = "pull-request-merge"
    else:
        failures.append(f"unsupported CI event: {event_name}")

    details = {
        "actual_sha": actual_sha,
        "expected_sha": expected_sha,
        "event_source_sha": derived_event_sha,
        "event_after": payload.get("after") if isinstance(payload.get("after"), str) else None,
        "pull_request_head_sha": (
            payload.get("pull_request", {}).get("head", {}).get("sha")
            if event_name == "pull_request"
            else None
        ),
        "pull_request_merge_sha": (
            payload.get("pull_request", {}).get("merge_commit_sha")
            if event_name == "pull_request"
            else None
        ),
        "github_sha": github_sha,
        "github_sha_kind": github_sha_kind,
        "release_id": release_id,
    }
    return failures, details


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    event_path = Path(os.environ.get("GITHUB_EVENT_PATH", ""))
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
        actual_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=10
        ).strip()
        failures, details = evaluate(
            actual_sha=actual_sha,
            expected_sha=args.expected_sha,
            github_sha=os.environ.get("GITHUB_SHA", ""),
            event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
            payload=payload,
            release_id=args.release_id,
        )
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        failures = [f"provenance input could not be read: {error}"]
        details = {
            "actual_sha": None,
            "expected_sha": args.expected_sha,
            "event_source_sha": None,
            "event_after": None,
            "pull_request_head_sha": None,
            "pull_request_merge_sha": None,
            "github_sha": os.environ.get("GITHUB_SHA"),
            "github_sha_kind": "unknown",
            "release_id": args.release_id,
        }

    document = {
        "schema_version": "ci-provenance-v1",
        "observed_at": utc_now(),
        "job": args.job,
        "event_name": os.environ.get("GITHUB_EVENT_NAME"),
        "status": "failed" if failures else "passed",
        **details,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"job": args.job, "status": document["status"], "source_sha": details["expected_sha"]}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
