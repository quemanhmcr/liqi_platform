#!/usr/bin/env python3
"""Owner-invoked wrapper that runs one exact pending Cargo gate and records evidence."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

try:
    from scripts.operations.owner_build_evidence import ROOT, SCHEMA, repository_ref, sha256
    from scripts.operations.redaction import redact
except ModuleNotFoundError:
    from owner_build_evidence import ROOT, SCHEMA, repository_ref, sha256
    from redaction import redact

REGISTRY = ROOT / "operations/integration/provider-gates-v0.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def windows_msvc_linker_error(platform: str, rust_host: str, linker: str | None) -> str | None:
    if platform != "win32" or not rust_host.endswith("-windows-msvc"):
        return None
    if linker is None:
        return "MSVC Rust host requires a discoverable Microsoft link.exe"
    normalized = linker.replace("\\", "/").lower()
    if normalized.endswith("/git/usr/bin/link.exe"):
        return (
            "MSVC Rust host resolved link.exe to the Git Unix linker; activate MSVC Build Tools "
            "or use the exact Rust GNU host toolchain before generating owner evidence"
        )
    return None


def validate_build_environment(argv: list[str]) -> bool:
    toolchain = argv[1] if len(argv) > 1 and argv[1].startswith("+") else None
    command = ["rustc", *([toolchain] if toolchain else []), "-vV"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        print("ERROR owner-build: unable to inspect the registered Rust toolchain", file=sys.stderr)
        return False
    host = next(
        (line.partition(":")[2].strip() for line in completed.stdout.splitlines() if line.startswith("host:")),
        "",
    )
    error = windows_msvc_linker_error(sys.platform, host, shutil.which("link.exe"))
    if error:
        print(f"ERROR owner-build environment: {error}", file=sys.stderr)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-id", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    tracked_status = subprocess.check_output(
        ["git", "status", "--short", "--untracked-files=no"], cwd=ROOT, text=True
    ).strip()
    if tracked_status:
        print("ERROR owner-build: tracked working tree must be clean", file=sys.stderr)
        return 64
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    registry = load(REGISTRY)
    gate = next((item for item in registry["gates"] if item["id"] == args.gate_id), None)
    if gate is None or gate.get("provider_state") != "pending-owner-build":
        print("ERROR owner-build: gate is not a pending-owner-build provider gate", file=sys.stderr)
        return 64
    if gate.get("mutation_class") != "read-only" or gate.get("argv", [None])[0] != "cargo":
        print("ERROR owner-build: only exact read-only Cargo owner gates are supported", file=sys.stderr)
        return 64
    if not validate_build_environment(gate["argv"]):
        return 64

    output_dir = args.output_dir.resolve()
    try:
        output_dir.relative_to(ROOT.resolve())
    except ValueError:
        print("ERROR owner-build: output directory must be inside the repository", file=sys.stderr)
        return 64
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"{gate['id']}.log"
    result_path = output_dir / f"{gate['id']}.result.json"
    record_path = output_dir / f"{gate['id']}.json"

    started = now()
    try:
        completed = subprocess.run(
            gate["argv"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=gate["timeout_seconds"],
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + "\ncommand timed out"
    log_path.write_text(redact(f"STDOUT\n{stdout}\nSTDERR\n{stderr}"), encoding="utf-8", newline="\n")

    record: dict[str, Any] = {
        "schema_version": "owner-build-evidence-v0",
        "producer": "project-owner",
        "approval_ref": args.approval_ref,
        "gate_id": gate["id"],
        "git_sha": git_sha,
        "command": gate["argv"],
        "status": "passed" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "started_at": started,
        "completed_at": now(),
        "log_ref": repository_ref(log_path),
        "log_sha256": sha256(log_path),
    }
    if gate["result_mode"] == "stdout-json" and exit_code == 0:
        try:
            document = json.loads(stdout)
            result_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            record["result_ref"] = repository_ref(result_path)
            record["result_sha256"] = sha256(result_path)
        except json.JSONDecodeError as exc:
            record["status"] = "failed"
            record["exit_code"] = 65
            log_path.write_text(log_path.read_text(encoding="utf-8") + f"\nRESULT ERROR\n{redact(str(exc))}\n", encoding="utf-8", newline="\n")
            record["log_sha256"] = sha256(log_path)

    schema_errors = list(Draft202012Validator(load(SCHEMA), format_checker=FormatChecker()).iter_errors(record))
    if schema_errors:
        for error in schema_errors:
            print(f"ERROR owner-build evidence: {error.message}", file=sys.stderr)
        return 65
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(record_path)
    return int(record["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
