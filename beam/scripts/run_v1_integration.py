#!/usr/bin/env python3
"""Compose Senior 2's disposable database gates with Senior 1's root consumer E2E."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
RESULT_SCHEMA = ROOT / "contracts" / "runtime" / "runtime-integration-result-v1.schema.json"
BOUNDED_RUNNER = ROOT / "beam" / "scripts" / "run_bounded.py"
SAFE_DATABASE = re.compile(r"^liqi_v1_test_[a-z0-9_]{4,48}$")
SAFE_UPGRADE_DATABASE = re.compile(r"^liqi_v0_upgrade_test_[a-z0-9_]{4,48}$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ROLE_USERS = {"command": "liqi_api", "realtime": "liqi_realtime", "worker": "liqi_worker"}
REQUIRED_TOOLS = ("bash", "psql", "pg_prove", "createdb", "dropdb", "mix")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10
    ).strip()


def clean_worktree() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("cannot inspect Git worktree state")
    return result.stdout == ""


def parse_admin_url(value: str) -> dict[str, str]:
    parsed = urlsplit(value)
    database = unquote(parsed.path.removeprefix("/"))

    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("LIQI_TEST_DATABASE_URL must use postgres or postgresql")
    if parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("LIQI_TEST_DATABASE_URL must target a loopback disposable cluster")
    if not parsed.username:
        raise ValueError("LIQI_TEST_DATABASE_URL must include an administrative username")
    if parsed.password is not None:
        raise ValueError(
            "LIQI_TEST_DATABASE_URL must use trust authentication; password-bearing DSNs are unsupported"
        )
    if database not in {"postgres", "liqi_v1_ci"}:
        raise ValueError(
            "LIQI_TEST_DATABASE_URL must target postgres or the disposable liqi_v1_ci database"
        )
    if parsed.query or parsed.fragment:
        raise ValueError("LIQI_TEST_DATABASE_URL query and fragment are forbidden")

    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "username": unquote(parsed.username),
        "admin_database": database,
    }


def role_url(admin: dict[str, str], username: str, database: str) -> str:
    host = admin["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return (
        f"postgresql://{quote(username, safe='')}@{host}:{admin['port']}/"
        f"{quote(database, safe='')}"
    )


def role_urls(admin: dict[str, str], database: str) -> dict[str, str]:
    return {
        role: role_url(admin, username, database)
        for role, username in ROLE_USERS.items()
    }


def file_reference(path: Path) -> str:
    return "file://" + path.resolve().as_posix()


def redact(value: str, secrets_to_redact: list[str]) -> str:
    result = value
    for secret in sorted((item for item in secrets_to_redact if item), key=len, reverse=True):
        result = result.replace(secret, "<redacted>")
    return result[-8000:]


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def bounded(
    command: list[str],
    env: dict[str, str],
    timeout: int,
    secrets_to_redact: list[str],
) -> tuple[int, str]:
    argv = [sys.executable, str(BOUNDED_RUNNER), "--timeout", str(timeout), "--", *command]
    kwargs: dict[str, Any] = {
        "cwd": ROOT,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(argv, **kwargs)
    try:
        output, _ = process.communicate(timeout=timeout + 30)
        return process.returncode, redact(output or "", secrets_to_redact)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            output = ""
        return 124, redact((output or "") + f"\ncommand timed out after {timeout}s", secrets_to_redact)


def cleanup_database(
    env: dict[str, str], database: str, secrets_to_redact: list[str]
) -> tuple[int, str]:
    return bounded(
        [
            shutil.which("dropdb") or "dropdb",
            "--if-exists",
            "--force",
            "--maintenance-db=postgres",
            database,
        ],
        env,
        60,
        secrets_to_redact,
    )


def result_document(
    status: str, checks: list[dict[str, str]], blockers: list[str]
) -> dict[str, Any]:
    return {
        "schema_version": "runtime-integration-result-v1",
        "git_sha": git_sha(),
        "observed_at": utc_now(),
        "status": status,
        "database_target": "disposable-redacted",
        "checks": checks,
        "blockers": blockers,
    }


def write_result(path: Path, document: dict[str, Any]) -> None:
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
    )
    if errors:
        raise RuntimeError(
            "invalid runtime integration result: "
            + "; ".join(error.message for error in errors)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")


def emit(
    output: Path,
    status: str,
    checks: list[dict[str, str]],
    blockers: list[str],
) -> int:
    write_result(output, result_document(status, checks, blockers))
    return 0 if status == "passed" else 2 if status == "blocked" else 1


def main_with_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if not clean_worktree():
            return emit(
                args.output,
                "failed",
                [{"name": "clean-worktree", "status": "failed"}],
                ["integration evidence requires a clean exact Git SHA"],
            )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return emit(
            args.output,
            "failed",
            [{"name": "clean-worktree", "status": "failed"}],
            [str(error)],
        )

    database_url = os.environ.get("LIQI_TEST_DATABASE_URL")
    if not database_url:
        return emit(
            args.output,
            "blocked",
            [{"name": "disposable-database-input", "status": "blocked"}],
            ["LIQI_TEST_DATABASE_URL is required"],
        )

    suffix = secrets.token_hex(4)
    database = os.environ.get("LIQI_TEST_DATABASE", f"liqi_v1_test_runtime_{suffix}")
    upgrade_database = os.environ.get(
        "LIQI_V0_UPGRADE_DATABASE", f"liqi_v0_upgrade_test_runtime_{suffix}"
    )
    if not SAFE_DATABASE.fullmatch(database) or not SAFE_UPGRADE_DATABASE.fullmatch(
        upgrade_database
    ):
        return emit(
            args.output,
            "failed",
            [{"name": "disposable-database-name", "status": "failed"}],
            ["disposable database names do not match the protected prefixes"],
        )

    try:
        admin = parse_admin_url(database_url)
    except ValueError as error:
        return emit(
            args.output,
            "failed",
            [{"name": "disposable-database-input", "status": "failed"}],
            [str(error)],
        )

    missing_tools = [name for name in REQUIRED_TOOLS if not shutil.which(name)]
    if missing_tools:
        return emit(
            args.output,
            "blocked",
            [{"name": "disposable-database-tooling", "status": "blocked"}],
            [f"required disposable database commands are missing: {', '.join(missing_tools)}"],
        )

    urls = role_urls(admin, database)
    secrets_to_redact = [
        database_url,
        *urls.values(),
        "local-disposable-trust-only",
    ]
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": admin["host"],
            "PGPORT": admin["port"],
            "PGUSER": admin["username"],
            "PGDATABASE": admin["admin_database"],
            "PGSSLMODE": "disable",
            "LIQI_TEST_DATABASE": database,
            "LIQI_V0_UPGRADE_DATABASE": upgrade_database,
            "LIQI_RUN_BEAM_INTEGRATION": "1",
            "MIX": os.environ.get("MIX", "mix"),
        }
    )
    for name in (
        "PGPASSWORD",
        "PGPASSFILE",
        "PGSERVICE",
        "PGSERVICEFILE",
        "LIQI_API_DATABASE_SECRET_REF",
        "LIQI_REALTIME_DATABASE_SECRET_REF",
        "LIQI_WORKER_DATABASE_SECRET_REF",
    ):
        env.pop(name, None)

    checks: list[dict[str, str]] = [
        {"name": "clean-worktree", "status": "passed"},
        {"name": "disposable-database-input", "status": "passed"},
        {"name": "disposable-database-tooling", "status": "passed"},
    ]
    blockers: list[str] = []
    provider_ok = False
    consumer_ok = False
    cleanup_ok = False

    preclean_results = [
        cleanup_database(env, database, secrets_to_redact),
        cleanup_database(env, upgrade_database, secrets_to_redact),
    ]
    preclean_ok = all(code == 0 for code, _log in preclean_results)
    checks.append(
        {
            "name": "disposable-database-preclean",
            "status": "passed" if preclean_ok else "failed",
        }
    )
    if not preclean_ok:
        blockers.append("failed to prepare protected disposable database names")
        for code, log in preclean_results:
            if code != 0:
                print(log, file=sys.stderr)
    else:
        with tempfile.TemporaryDirectory(prefix="liqi-runtime-db-") as directory:
            temp = Path(directory)
            bundle_path = temp / "database-role-urls.json"
            bundle_path.write_text(
                json.dumps(urls, separators=(",", ":")) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            try:
                bundle_path.chmod(0o600)
            except OSError:
                pass

            provider_env = env.copy()
            provider_env["LIQI_TEST_DATABASE_ROLE_URLS_REF"] = file_reference(bundle_path)
            provider_rc, provider_log = bounded(
                [
                    shutil.which("bash") or "bash",
                    "database/tests/integration/run_database_tests.sh",
                ],
                provider_env,
                1_800,
                secrets_to_redact,
            )
            provider_ok = provider_rc == 0
            checks.append(
                {
                    "name": "database-provider-integration",
                    "status": "passed" if provider_ok else "failed",
                }
            )
            if not provider_ok:
                blockers.append("Senior 2 database and BEAM provider integration suite failed")
                print(provider_log, file=sys.stderr)
            else:
                consumer_env = env.copy()
                consumer_env.update(
                    {
                        "LIQI_DATABASE_INTEGRATION": "1",
                        "LIQI_TEST_DATABASE_ROLE_URLS_REF": file_reference(bundle_path),
                        "MIX_ENV": "test",
                        "MIX_BUILD_PATH": str(temp / "root-build"),
                    }
                )
                consumer_rc, consumer_log = bounded(
                    [
                        shutil.which("mix") or "mix",
                        "test",
                        "beam/test/liqi/persistence/database_provider_integration_test.exs",
                        "--seed",
                        "0",
                    ],
                    consumer_env,
                    600,
                    secrets_to_redact,
                )
                consumer_ok = consumer_rc == 0
                checks.append(
                    {
                        "name": "runtime-provider-consumer",
                        "status": "passed" if consumer_ok else "failed",
                    }
                )
                if not consumer_ok:
                    blockers.append("Senior 1 Ecto/Oban provider consumer integration failed")
                    print(consumer_log, file=sys.stderr)
    cleanup_results = [
        cleanup_database(env, database, secrets_to_redact),
        cleanup_database(env, upgrade_database, secrets_to_redact),
    ]
    cleanup_ok = all(code == 0 for code, _log in cleanup_results)
    checks.append(
        {
            "name": "disposable-database-cleanup",
            "status": "passed" if cleanup_ok else "failed",
        }
    )
    if not cleanup_ok:
        blockers.append("disposable database cleanup failed")
        for code, log in cleanup_results:
            if code != 0:
                print(log, file=sys.stderr)

    status = "passed" if provider_ok and consumer_ok and cleanup_ok else "failed"
    return emit(args.output, status, checks, blockers)


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    raise SystemExit(main())
