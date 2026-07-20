#!/usr/bin/env python3
"""Invoke provider-owned V1 commands and emit checkpoint evidence.

The runner never implements provider logic and never executes an automatic live
mutation. Missing provider publication or approval remains owner-attributed
`blocked` evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from operations.bin.readiness_v1_common import (
        ROOT,
        blocker,
        combine_status,
        git_sha,
        load_json,
        redact,
        relative_ref,
        safe_repo_path,
        utc_now,
        validate_document,
        write_json,
    )
except ModuleNotFoundError:
    from readiness_v1_common import (
        ROOT,
        blocker,
        combine_status,
        git_sha,
        load_json,
        redact,
        relative_ref,
        safe_repo_path,
        utc_now,
        validate_document,
        write_json,
    )

DEFAULT_REGISTRY = ROOT / "operations" / "readiness" / "provider-gates-v1.json"
CHECKPOINT_SCHEMA = ROOT / "contracts" / "readiness" / "checkpoint-result-v1.schema.json"
ENV_PATTERN = re.compile(r"\{env:([A-Z][A-Z0-9_]{1,63})\}")
OPTIONAL_ENV_ARG_PATTERN = re.compile(
    r"^\{optional-env-arg:(--[a-z0-9][a-z0-9-]{1,63}):([A-Z][A-Z0-9_]{1,63})\}$"
)
STAGES = ("source", "integration", "artifact", "live-staging", "promotion", "cutover", "post-cutover")


def resolve_bash(argv: list[str]) -> list[str]:
    if os.name != "nt" or not argv or argv[0] != "bash":
        return argv
    git = shutil.which("git")
    candidates: list[Path] = []
    if git:
        git_path = Path(git).resolve()
        if len(git_path.parents) >= 3:
            candidates.append(git_path.parents[2] / "usr" / "bin" / "bash.exe")
    if os.environ.get("ProgramFiles"):
        candidates.append(Path(os.environ["ProgramFiles"]) / "Git" / "usr" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate), *argv[1:]]
    raise RuntimeError("Git Bash is required for provider shell commands on Windows")


def expand(parts: list[str], output: Path) -> tuple[list[str], str]:
    actual: list[str] = []
    displayed: list[str] = []
    for part in parts:
        if part.startswith("{optional-env-arg:"):
            match = OPTIONAL_ENV_ARG_PATTERN.fullmatch(part)
            if match is None:
                raise ValueError(f"invalid optional environment argument token: {part}")
            flag, name = match.groups()
            value = os.environ.get(name)
            if value:
                actual.extend((flag, value))
                displayed.extend((flag, f"<env:{name}>"))
            continue
        resolved = part.replace("{output}", str(output))
        display = part.replace("{output}", output.name)
        for name in ENV_PATTERN.findall(part):
            resolved = resolved.replace(f"{{env:{name}}}", os.environ.get(name, ""))
            display = display.replace(f"{{env:{name}}}", f"<env:{name}>")
        actual.append(resolved)
        displayed.append(display)
    return actual, " ".join(displayed)


def classify_provider_outcome(returncode: int, document_status: str | None) -> tuple[str, str | None, str | None]:
    if document_status == "blocked":
        return (
            "blocked",
            "PROVIDER_GATE_BLOCKED",
            f"provider emitted blocked evidence and exited {returncode}",
        )
    if document_status == "failed":
        return (
            "failed",
            "PROVIDER_GATE_FAILED",
            f"provider emitted failed evidence and exited {returncode}",
        )
    if document_status == "passed" and returncode != 0:
        return (
            "failed",
            "PROVIDER_RESULT_EXIT_MISMATCH",
            f"provider emitted passed evidence but exited {returncode}",
        )
    if returncode != 0:
        return (
            "failed",
            "PROVIDER_GATE_FAILED",
            f"provider command exited {returncode}",
        )
    return "passed", None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--release-id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / ".artifacts" / "v1-provider-gates")
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--allow-build", action="store_true")
    parser.add_argument("--allow-disposable-database", action="store_true")
    parser.add_argument("--allow-live-read-only", action="store_true")
    parser.add_argument("--allow-isolated-restore", action="store_true")
    parser.add_argument("--approval-ref")
    args = parser.parse_args()

    sha = git_sha()
    release_id = args.release_id or f"liqi-v1-{args.stage}-{sha[:12]}"
    registry = load_json(args.registry.resolve())
    evidence_dir = args.evidence_dir.resolve() / f"{args.stage}-{sha[:12]}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    provider_results: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    evidence_refs: list[str] = []

    def add_result(gate: dict[str, Any], status: str, evidence_ref: str | None, code: str | None = None, message: str | None = None) -> None:
        provider_results.append({"owner": gate["owner"], "seam": gate["seam"], "status": status, "evidence_ref": evidence_ref})
        if evidence_ref:
            evidence_refs.append(evidence_ref)
        if code and message:
            blockers.append(blocker(gate["owner"], gate["seam"], code, status if status in {"blocked", "failed"} else "failed", message, gate["action_required"]))

    for gate in registry["gates"]:
        if args.stage not in gate["stages"]:
            continue
        gate_output = evidence_dir / f"{gate['id']}.json"
        log_path = evidence_dir / f"{gate['id']}.log"
        state = gate["provider_state"]
        if state != "available":
            if state == "pending-integration":
                code = "PROVIDER_COMMIT_NOT_INTEGRATED"
                message = f"provider commit {gate['provider_commit']} on {gate['provider_branch']} is published but not integrated"
            elif state == "pending-live-evidence":
                code = "PROVIDER_LIVE_EVIDENCE_PENDING"
                message = f"provider command is integrated but exact-release live evidence is pending for {gate['provider_commit']}"
            else:
                code = "PROVIDER_SEAM_UNPUBLISHED"
                message = "provider has not published a consumer-ready command"
            add_result(gate, "blocked", None, code, message)
            continue
        missing_paths = [path for path in gate["required_paths"] if not safe_repo_path(path).exists()]
        if missing_paths:
            add_result(gate, "failed", None, "PROVIDER_PUBLICATION_INCOMPLETE", f"available provider gate is missing paths: {missing_paths}")
            continue
        missing_env = [name for name in gate.get("required_environment", []) if not os.environ.get(name)]
        if missing_env:
            add_result(gate, "blocked", None, "PROVIDER_INPUT_MISSING", f"required environment is missing: {missing_env}")
            continue
        mutation = gate["mutation_class"]
        blocked_reason: str | None = None
        if mutation == "approved-live-mutation":
            blocked_reason = "automatic live mutations are forbidden in the readiness provider runner"
        elif mutation == "build" and not args.allow_build:
            blocked_reason = "build execution requires --allow-build"
        elif mutation == "disposable-database" and not args.allow_disposable_database:
            blocked_reason = "disposable database execution requires --allow-disposable-database"
        elif mutation == "live-read-only" and not args.allow_live_read_only:
            blocked_reason = "live read-only execution requires --allow-live-read-only"
        elif mutation == "isolated-restore" and (not args.allow_isolated_restore or not args.approval_ref):
            blocked_reason = "isolated restore requires --allow-isolated-restore and --approval-ref"
        if blocked_reason:
            add_result(gate, "blocked", None, "PROVIDER_EXECUTION_NOT_APPROVED", blocked_reason)
            continue

        if mutation == "isolated-restore":
            os.environ.setdefault("LIQI_RESTORE_APPROVAL_REF", args.approval_ref or "")
        try:
            argv, displayed = expand(gate["argv"], gate_output)
        except ValueError as exc:
            add_result(gate, "failed", None, "PROVIDER_REGISTRY_INVALID", str(exc))
            continue
        started = time.monotonic()
        try:
            argv = resolve_bash(argv)
            completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=gate["timeout_seconds"], check=False, env=os.environ.copy())
            duration_ms = round((time.monotonic() - started) * 1000)
            log_path.write_text(redact(f"COMMAND\n{displayed}\nDURATION_MS\n{duration_ms}\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}"), encoding="utf-8", newline="\n")
            log_ref = relative_ref(log_path)
            output_ref = log_ref
            document_status: str | None = None
            result_schema = gate.get("result_schema")
            if result_schema:
                try:
                    loaded = load_json(gate_output)
                    if not isinstance(loaded, dict):
                        raise ValueError(f"{gate['id']} result must be a JSON object")
                    document = loaded
                    schema_errors = validate_document(safe_repo_path(result_schema), document, gate["id"])
                    if document.get("git_sha") not in {None, sha}:
                        schema_errors.append(f"{gate['id']}.git_sha does not match {sha}")
                    if document.get("release_id") not in {None, release_id}:
                        schema_errors.append(f"{gate['id']}.release_id does not match {release_id}")
                    if schema_errors:
                        raise ValueError("; ".join(schema_errors))
                    output_ref = relative_ref(gate_output)
                    observed_status = document.get("status")
                    document_status = observed_status if isinstance(observed_status, str) else None
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    log_path.write_text(log_path.read_text(encoding="utf-8") + f"\nRESULT_VALIDATION\n{redact(str(exc))}\n", encoding="utf-8", newline="\n")
                    add_result(gate, "failed", log_ref, "PROVIDER_RESULT_INVALID", str(exc))
                    continue
            status, code, message = classify_provider_outcome(completed.returncode, document_status)
            if message:
                message = f"{message}; see {output_ref}"
            add_result(gate, status, output_ref, code, message)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            log_path.write_text(redact(str(exc)) + "\n", encoding="utf-8", newline="\n")
            add_result(gate, "failed", relative_ref(log_path), "PROVIDER_GATE_EXECUTION_FAILED", str(exc))

    statuses = [item["status"] for item in provider_results]
    overall = combine_status(statuses) if statuses else "blocked"
    if not provider_results:
        blockers.append(blocker("Senior 5", "operations/readiness/provider-gates-v1.json", "CHECKPOINT_HAS_NO_GATES", "blocked", f"no provider gates are registered for {args.stage}", "Register at least one provider-owned command for this checkpoint."))
    result = {
        "schema_version": "checkpoint-result-v1",
        "name": args.stage,
        "git_sha": sha,
        "release_id": release_id,
        "observed_at": utc_now(),
        "status": overall,
        "provider_results": provider_results or [{"owner": "Senior 5", "seam": "provider gate registry", "status": "blocked", "evidence_ref": None}],
        "evidence_refs": sorted(set(evidence_refs)),
        "blockers": blockers,
    }
    schema_errors = validate_document(CHECKPOINT_SCHEMA, result, "checkpoint-result-v1")
    if schema_errors:
        for message in schema_errors:
            print(f"ERROR provider-gates-v1: {message}", file=sys.stderr)
        return 65
    write_json(args.output.resolve(), result)
    print(f"V1 checkpoint {args.stage}: {overall} -> {args.output}")
    if overall == "failed":
        return 1
    if overall == "blocked" and not args.allow_blocked:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
