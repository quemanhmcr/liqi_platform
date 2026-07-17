#!/usr/bin/env python3
"""Invoke provider-owned gates and emit integration-result-v0 evidence.

The runner never substitutes provider logic. Missing or unpublished seams become
owner-attributed blocked results. Host and OCI mutations are always rejected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
try:
    from scripts.operations.redaction import redact
except ModuleNotFoundError:  # direct script execution
    from redaction import redact

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "operations" / "integration" / "provider-gates-v0.json"
RESULT_SCHEMA = ROOT / "contracts" / "operations" / "integration-result-v0.schema.json"
ENV_PLACEHOLDER = re.compile(r"\{env:([A-Z][A-Z0-9_]{1,63})\}")
def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_path(relative: str) -> Path:
    candidate = (ROOT / relative).resolve()
    candidate.relative_to(ROOT.resolve())
    return candidate


def git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def expand_argv(parts: list[str], output_path: Path) -> tuple[list[str], str]:
    actual: list[str] = []
    displayed: list[str] = []
    for part in parts:
        resolved = part.replace("{output}", str(output_path))
        display = part.replace("{output}", output_path.name)
        for name in ENV_PLACEHOLDER.findall(part):
            value = os.environ.get(name, "")
            resolved = resolved.replace(f"{{env:{name}}}", value)
            display = display.replace(f"{{env:{name}}}", f"<env:{name}>")
        actual.append(resolved)
        displayed.append(display)
    return actual, " ".join(displayed)


def check_result(gate: dict[str, Any], status: str, command: str, exit_code: int | None,
                 duration_ms: int, output_ref: str | None, failure_class: str | None) -> dict[str, Any]:
    return {
        "owner": gate["owner"],
        "seam": gate["seam"],
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "output_ref": output_ref,
        "failure_class": failure_class,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--stage", choices=("source", "integration", "promotion"), required=True)
    parser.add_argument("--environment", choices=("development", "staging", "production"), default="development")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / ".artifacts" / "provider-gates")
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--allow-disposable-database", action="store_true")
    args = parser.parse_args()

    registry = load_json(args.registry.resolve())
    sha = git_sha()
    started = now()
    integration_id = f"int-{args.stage}-{sha[:12]}"
    evidence_dir = args.evidence_dir.resolve() / integration_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    violations: list[dict[str, str]] = []

    for gate in registry["gates"]:
        if args.stage not in gate["stages"]:
            continue
        gate_output = evidence_dir / f"{gate['id']}.json"
        argv, command = expand_argv(gate["argv"], gate_output)
        missing_paths = [path for path in gate["required_paths"] if not safe_path(path).exists()]
        missing_env = [name for name in gate.get("required_environment", []) if not os.environ.get(name)]
        blocked_reason: str | None = None
        if gate["provider_state"] != "available":
            blocked_reason = f"provider state is {gate['provider_state']}"
        elif missing_paths:
            blocked_reason = f"required provider paths are missing: {missing_paths}"
        elif missing_env:
            blocked_reason = f"required environment is missing: {missing_env}"
        elif gate["mutation_class"] in {"host-mutation", "oci-mutation"}:
            blocked_reason = "host and OCI mutations are forbidden in provider validation"
        elif gate["mutation_class"] == "disposable-database" and not args.allow_disposable_database:
            blocked_reason = "disposable database mutation requires --allow-disposable-database"

        if blocked_reason:
            results.append(check_result(gate, "blocked", command, None, 0, None, gate["failure_class"]))
            violations.append({
                "owner": gate["owner"],
                "seam": gate["seam"],
                "code": "PROVIDER_GATE_BLOCKED",
                "message": blocked_reason,
                "action_required": gate["action_required"],
            })
            continue

        gate_started = time.monotonic()
        log_path = evidence_dir / f"{gate['id']}.log"
        try:
            completed = subprocess.run(
                argv,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=gate["timeout_seconds"],
                check=False,
                env=os.environ.copy(),
            )
            duration_ms = round((time.monotonic() - gate_started) * 1000)
            redacted = redact(f"STDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}")
            log_path.write_text(redacted, encoding="utf-8", newline="\n")
            output_ref = log_path.relative_to(ROOT).as_posix() if log_path.is_relative_to(ROOT) else log_path.as_posix()
            status = "passed" if completed.returncode == 0 else "failed"
            if status == "passed" and gate["result_mode"] == "json-file":
                result_path = evidence_dir / f"{gate['id']}.json"
                try:
                    load_json(result_path)
                except (OSError, json.JSONDecodeError) as exc:
                    status = "failed"
                    completed = subprocess.CompletedProcess(argv, 65, completed.stdout, f"invalid result JSON: {exc}")
            results.append(check_result(
                gate, status, command, completed.returncode, duration_ms, output_ref,
                None if status == "passed" else gate["failure_class"]
            ))
            if status == "failed":
                violations.append({
                    "owner": gate["owner"],
                    "seam": gate["seam"],
                    "code": "PROVIDER_GATE_FAILED",
                    "message": f"provider command failed; see {output_ref}",
                    "action_required": gate["action_required"],
                })
        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.monotonic() - gate_started) * 1000)
            log_path.write_text(redact(f"provider command timed out: {exc}"), encoding="utf-8", newline="\n")
            output_ref = log_path.relative_to(ROOT).as_posix() if log_path.is_relative_to(ROOT) else log_path.as_posix()
            results.append(check_result(gate, "failed", command, 124, duration_ms, output_ref, gate["failure_class"]))
            violations.append({
                "owner": gate["owner"],
                "seam": gate["seam"],
                "code": "PROVIDER_GATE_TIMEOUT",
                "message": f"provider command exceeded {gate['timeout_seconds']} seconds",
                "action_required": gate["action_required"],
            })

    statuses = [result["status"] for result in results]
    overall = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
    result = {
        "schema_version": "integration-result-v0",
        "integration_id": integration_id,
        "release_id": f"liqi-{args.stage}-{sha[:12]}",
        "git_sha": sha,
        "environment": args.environment,
        "mode": "provider",
        "started_at": started,
        "completed_at": now(),
        "overall_status": overall,
        "provider_results": results,
        "gates": [{
            "owner": "Senior 4",
            "seam": "operations/integration/provider-gates-v0.json",
            "command": "scripts/operations/run_provider_gates.py",
            "status": "passed",
            "exit_code": 0,
            "duration_ms": 0,
            "output_ref": None,
            "failure_class": None,
        }],
        "capacity": {
            "status": "blocked",
            "hard_limit_ocpu": 4,
            "hard_limit_memory_mib": 24576,
            "reserved_ocpu": 1,
            "reserved_memory_mib": 4096,
            "declared_ocpu": 0,
            "declared_memory_mib": 0,
            "disk_budget_gib": 0,
        },
        "recovery": {
            "status": "not-applicable" if args.stage == "source" else "blocked",
            "backup_age_seconds": None,
            "wal_archive_lag_seconds": None,
            "restore_verified_at": None,
            "evidence_ref": None,
        },
        "platform_probe": {
            "status": "not-run",
            "result_ref": None,
            "release_id_observed": None,
        },
        "violations": violations,
    }
    schema = load_json(RESULT_SCHEMA)
    schema_errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result),
        key=lambda item: list(item.absolute_path),
    )
    if schema_errors:
        for error in schema_errors:
            print(f"ERROR integration-result: {'.'.join(map(str, error.absolute_path))}: {error.message}", file=sys.stderr)
        return 65
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"provider gates {overall}: {args.output}")
    if overall == "failed":
        return 1
    if overall == "blocked" and not args.allow_blocked:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
