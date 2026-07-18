#!/usr/bin/env python3
"""Run bounded V1 native safety gates and emit exact-SHA evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "native" / "native-safety-result-v1.schema.json"
REQUIRED_COMMANDS = ("bash", "cargo", "rustc", "rustup", "elixir", "mix", "python")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def version(argv: list[str]) -> str:
    completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30)
    text = (completed.stdout or completed.stderr).strip().replace("\r", "")
    if completed.returncode or not text:
        return "unavailable"
    return " | ".join(line.strip() for line in text.splitlines() if line.strip())[:128]


def write_result(path: Path, result: dict[str, Any]) -> None:
    errors = list(
        Draft202012Validator(
            json.loads(SCHEMA.read_text(encoding="utf-8")),
            format_checker=FormatChecker(),
        ).iter_errors(result)
    )
    if errors:
        raise ValueError("; ".join(f"{list(error.absolute_path)}: {error.message}" for error in errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        output.relative_to(ROOT)
    except ValueError:
        parser.error("--output must be inside the repository so evidence references remain portable")

    started_at = now()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    fuzz_seconds_text = os.environ.get("LIQI_FUZZ_SECONDS", "300")
    if not fuzz_seconds_text.isdigit() or not 1 <= int(fuzz_seconds_text) <= 3600:
        parser.error("LIQI_FUZZ_SECONDS must be an integer from 1 through 3600")
    fuzz_seconds = int(fuzz_seconds_text)
    log_dir = output.parent / f"{output.stem}.logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    toolchain = {
        "rust": version(["rustc", "+1.97.1", "--version"]) if shutil.which("rustc") else "unavailable",
        "cargo": version(["cargo", "+1.97.1", "--version"]) if shutil.which("cargo") else "unavailable",
        "elixir": version(["elixir", "--version"]) if shutil.which("elixir") else "unavailable",
        "otp": version([
            "erl",
            "-noshell",
            "-eval",
            'Otp=erlang:system_info(otp_release), {ok,B}=file:read_file(filename:join([code:root_dir(),"releases",Otp,"OTP_VERSION"])), io:format("~s", [string:trim(binary_to_list(B))]), halt().',
        ]) if shutil.which("erl") else "unavailable",
        "rustler": "0.38.0",
        "nif_abi": "2.15",
    }

    missing = [command for command in REQUIRED_COMMANDS if not shutil.which(command)]
    if platform.system() != "Linux" or missing:
        reason = "Linux is required" if platform.system() != "Linux" else f"missing commands: {missing}"
        log = log_dir / "environment.log"
        log.write_text(reason + "\n", encoding="utf-8", newline="\n")
        checks.append({"id": "environment", "status": "blocked", "command": ["native-safety-environment"], "duration_ms": 0, "log_ref": relative(log)})
        result = {
            "schema_version": "native-safety-result-v1",
            "git_sha": sha,
            "status": "blocked",
            "started_at": started_at,
            "completed_at": now(),
            "toolchain": toolchain,
            "checks": checks,
            "fuzz_seconds": fuzz_seconds,
            "property_cases": 2000,
            "failures": [reason],
        }
        write_result(output, result)
        print(json.dumps({"validation": "native-safety-v1", "status": "blocked", "reason": reason, "output": relative(output)}, sort_keys=True, separators=(",", ":")))
        return 69

    expected_versions = {"rust": "rustc 1.97.1 ", "cargo": "cargo 1.97.1 ", "elixir": "Elixir 1.20.2", "otp": "28.5.0.3"}
    version_failures = []
    for name, expected in expected_versions.items():
        observed = toolchain[name]
        if name == "otp":
            matches = observed == expected
        else:
            matches = expected in observed
        if not matches:
            version_failures.append(f"{name} toolchain mismatch: expected {expected!r}, got {observed!r}")
    if version_failures:
        log = log_dir / "toolchain.log"
        log.write_text("\n".join(version_failures) + "\n", encoding="utf-8", newline="\n")
        checks.append({"id": "toolchain", "status": "failed", "command": ["verify-pinned-toolchain"], "duration_ms": 0, "log_ref": relative(log)})
        result = {
            "schema_version": "native-safety-result-v1",
            "git_sha": sha,
            "status": "failed",
            "started_at": started_at,
            "completed_at": now(),
            "toolchain": toolchain,
            "checks": checks,
            "fuzz_seconds": fuzz_seconds,
            "property_cases": 2000,
            "failures": version_failures,
        }
        write_result(output, result)
        print(json.dumps({"validation": "native-safety-v1", "status": "failed", "reason": "toolchain-mismatch", "output": relative(output)}, sort_keys=True, separators=(",", ":")))
        return 1

    tracked_status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
    ).strip()
    if tracked_status:
        failures.append("tracked worktree or index is dirty")

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LIQI_FUZZ_SECONDS"] = str(fuzz_seconds)
    env["MIX_BUILD_PATH"] = str(ROOT / ".artifacts" / "native-safety" / f"mix-build-{sha[:12]}")
    env["MIX_ENV"] = "test"

    commands: list[tuple[str, list[str], int]] = [
        ("contracts", [sys.executable, "contracts/native/validate_contracts.py"], 120),
        ("source", ["bash", "native/tests/run-source-validation.sh"], 1800),
        ("nif-release", ["cargo", "+1.97.1", "build", "--locked", "--profile", "nif-release", "-p", "liqi-sequence-diff-nif"], 900),
        ("mix-deps", ["mix", "deps.get", "--locked"], 900),
        ("runtime-native", ["mix", "test", "beam/test/liqi/runtime/native_test.exs", "--seed", "0"], 1200),
        ("fallback-diagnostic", ["mix", "test", "beam/test/liqi/web/probe_auth_test.exs", "--seed", "0"], 1200),
        ("deployment-adapter", [sys.executable, "-m", "unittest", "native.tests.test_deployment_manifest", "-v"], 120),
        ("fuzz", ["bash", "native/fuzz/run-fuzz.sh"], fuzz_seconds + 180),
        ("secret-scan", [sys.executable, "scripts/operations/scan_repository_secrets.py"], 300),
    ]

    for check_id, argv, timeout_seconds in commands:
        log = log_dir / f"{check_id}.log"
        begin = time.monotonic()
        status = "passed"
        try:
            completed = subprocess.run(
                argv,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            content = f"COMMAND\n{' '.join(argv)}\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}"
            log.write_text(content, encoding="utf-8", newline="\n")
            if completed.returncode:
                status = "failed"
                failures.append(f"{check_id} exited {completed.returncode}; see {relative(log)}")
        except (OSError, subprocess.TimeoutExpired) as error:
            status = "failed"
            log.write_text(str(error) + "\n", encoding="utf-8", newline="\n")
            failures.append(f"{check_id} could not complete; see {relative(log)}")
        checks.append(
            {
                "id": check_id,
                "status": status,
                "command": argv,
                "duration_ms": round((time.monotonic() - begin) * 1000),
                "log_ref": relative(log),
            }
        )
        if status != "passed":
            break

    status = "failed" if failures else "passed"
    result = {
        "schema_version": "native-safety-result-v1",
        "git_sha": sha,
        "status": status,
        "started_at": started_at,
        "completed_at": now(),
        "toolchain": toolchain,
        "checks": checks,
        "fuzz_seconds": fuzz_seconds,
        "property_cases": 2000,
        "failures": failures,
    }
    write_result(output, result)
    print(json.dumps({"validation": "native-safety-v1", "status": status, "git_sha": sha, "output": relative(output)}, sort_keys=True, separators=(",", ":")))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"native safety gate failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
