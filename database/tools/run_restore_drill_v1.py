#!/usr/bin/env python3
"""Run the provider-owned V1 isolated restore/PITR drill and emit readiness evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
RECOVERY_SCHEMA = ROOT / "contracts/readiness/recovery-result-v1.schema.json"
BACKUP_SCHEMA = ROOT / "contracts/database/backup-metadata-v1.schema.json"
BACKUP_STATUS_SCHEMA = ROOT / "contracts/platform/database-backup-status-v0.schema.json"
RESTORE_SCHEMA = ROOT / "contracts/platform/database-restore-result-v0.schema.json"
BEAM_SCHEMA = ROOT / "contracts/database/restore-beam-probe-v1.schema.json"
ROLLBACK_SCHEMA = ROOT / "contracts/deployment/v0-rollback-compatibility-v1.schema.json"
TARGET_BASE = Path("/var/lib/liqi/recovery-exercises")
BACKUP_PREFIX = "pgbackrest://management/database-backup-repository/liqi/"
RELEASE_RE = re.compile(r"^liqi-v1-[a-z0-9][a-z0-9._-]{2,95}$")
BACKUP_LABEL_RE = re.compile(r"^[0-9]{8}-[0-9]{6}[FDI](?:_[0-9]{8}-[0-9]{6}[DI])?$")
TARGET_DB_RE = re.compile(r"^liqi_restore_[a-z0-9_]{3,48}$")


class DrillError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise DrillError("date-time must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DrillError(f"cannot read JSON evidence {path.name}") from exc
    if not isinstance(value, dict):
        raise DrillError(f"JSON evidence must be an object: {path.name}")
    return value


def validate(schema_path: Path, value: dict[str, Any], label: str) -> None:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "$"
        raise DrillError(f"{label} contract invalid at {location}: {errors[0].message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8", newline="\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def current_git_sha() -> str:
    value = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DrillError("current Git SHA is invalid")
    return value


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise DrillError(f"required protected input is missing: {name}")
    return value


def regular_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise DrillError(f"{label} must be a regular non-symlink file")
    return path


def evidence_ref(path: Path) -> str:
    return "file://" + path.resolve().as_posix()


def safe_message(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (text or exc.__class__.__name__)[:1000]


@dataclass
class Step:
    name: str
    owner: str
    status: str = "blocked"
    duration_ms: int = 0
    evidence_ref: str | None = None

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "evidence_ref": self.evidence_ref,
        }


class Drill:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.git_sha = current_git_sha()
        self.environment = required_env("LIQI_RECOVERY_ENVIRONMENT")
        if self.environment not in {"staging", "production"}:
            raise DrillError("LIQI_RECOVERY_ENVIRONMENT must be staging or production")
        if not RELEASE_RE.fullmatch(args.release_id):
            raise DrillError("release ID is invalid")
        if not args.approval_ref or len(args.approval_ref) > 512:
            raise DrillError("restore approval reference is invalid")
        self.backup_ref = required_env("LIQI_RECOVERY_BACKUP_REF")
        if not self.backup_ref.startswith(BACKUP_PREFIX):
            raise DrillError("backup reference must use the independent pgBackRest repository")
        self.label = self.backup_ref.removeprefix(BACKUP_PREFIX)
        if not BACKUP_LABEL_RE.fullmatch(self.label):
            raise DrillError("backup label is invalid")
        self.target_root = Path(required_env("LIQI_RECOVERY_TARGET_ROOT"))
        self.target_database = required_env("LIQI_RECOVERY_TARGET_DATABASE")
        if not TARGET_DB_RE.fullmatch(self.target_database):
            raise DrillError("restore target database identity is invalid")
        resolved_target = self.target_root.resolve(strict=False)
        try:
            resolved_target.relative_to(TARGET_BASE)
        except ValueError as exc:
            raise DrillError("restore target must be below the approved recovery root") from exc
        if resolved_target == TARGET_BASE or self.target_root.as_posix() != resolved_target.as_posix():
            raise DrillError("restore target must be a canonical child of the approved recovery root")
        self.target_root = resolved_target
        self.source_database_id = required_env("LIQI_RECOVERY_SOURCE_DATABASE_ID")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.source_database_id):
            raise DrillError("source database identity is invalid")
        self.pitr_target = parse_time(required_env("LIQI_RESTORE_TARGET_TIME"))
        if self.pitr_target > datetime.now(timezone.utc):
            raise DrillError("PITR target cannot be in the future")
        self.rollback_path = regular_file(Path(required_env("LIQI_V0_ROLLBACK_COMPATIBILITY")), "V0 rollback compatibility evidence")
        self.api_password_path = regular_file(Path(required_env("LIQI_DATABASE_API_PASSWORD_FILE")), "database API credential")
        self.release_bin = Path(required_env("LIQI_RECOVERY_RELEASE_BIN"))
        self.runtime_config = regular_file(Path(required_env("LIQI_RUNTIME_CONFIG_PATH")), "runtime configuration")
        self.backup_status = regular_file(Path(required_env("LIQI_BACKUP_STATUS_FILE")), "backup status evidence")
        self.backup_status_checksum = regular_file(Path(required_env("LIQI_BACKUP_STATUS_CHECKSUM_FILE")), "backup status checksum")
        expected_status_sha = self.backup_status_checksum.read_text(encoding="utf-8").strip().split()[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_status_sha) or expected_status_sha != sha256(self.backup_status):
            raise DrillError("backup status checksum verification failed")
        self.backup_status_document = load_json(self.backup_status)
        validate(BACKUP_STATUS_SCHEMA, self.backup_status_document, "backup status")
        if self.backup_status_document["recoveryReady"] is not True or self.backup_status_document["reasons"]:
            raise DrillError("backup and WAL archive are not recovery-ready")
        self.output = args.output.resolve()
        if self.output == self.target_root or self.target_root in self.output.parents:
            raise DrillError("final recovery evidence must be outside the disposable restore target")
        self.exercise_id = "restore-v1-" + uuid.uuid4().hex[:16]
        self.evidence_dir = self.output.parent / f"{self.exercise_id}-evidence"
        self.evidence_dir.mkdir(parents=True, exist_ok=False)
        os.chmod(self.evidence_dir, 0o700)
        self.started_at = utc_now()
        self.steps = {
            name: Step(name, "Senior 1" if name == "elixir-read-only-probe" else "Senior 2")
            for name in (
                "select-latest-valid-backup",
                "restore-isolated-target",
                "wal-pitr",
                "verify-migrations",
                "verify-platform-invariants",
                "elixir-read-only-probe",
                "cleanup",
            )
        }
        self.failures: list[str] = []
        self.target_prepared = False
        self.metadata: dict[str, Any] | None = None
        self.restore_result: dict[str, Any] | None = None
        self.beam_result: dict[str, Any] | None = None
        self.recovery_status_path = self.evidence_dir / "recovery-status.json"

    def command(self, name: str, argv: list[str], *, env: dict[str, str] | None = None, evidence: Path | None = None, timeout: int = 7200) -> None:
        step = self.steps[name]
        started = time.monotonic()
        log_path = self.evidence_dir / f"{name}.log"
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        step.duration_ms += round((time.monotonic() - started) * 1000)
        log_path.write_text(
            f"COMMAND\n{' '.join(argv)}\nRETURN_CODE\n{completed.returncode}\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}\n",
            encoding="utf-8",
            newline="\n",
        )
        os.chmod(log_path, 0o600)
        step.evidence_ref = evidence_ref(evidence if evidence and evidence.exists() else log_path)
        if completed.returncode != 0:
            step.status = "failed"
            raise DrillError(f"{name} command failed with exit {completed.returncode}; see {log_path.name}")
        step.status = "passed"

    def timed(self, name: str, operation: Callable[[], None], evidence: Path | None = None) -> None:
        step = self.steps[name]
        started = time.monotonic()
        operation()
        step.duration_ms += round((time.monotonic() - started) * 1000)
        step.status = "passed"
        if evidence:
            step.evidence_ref = evidence_ref(evidence)

    def validate_rollback(self) -> None:
        rollback = load_json(self.rollback_path)
        validate(ROLLBACK_SCHEMA, rollback, "V0 rollback compatibility")
        if rollback["database_provider_git_sha"] != self.git_sha:
            raise DrillError("V0 rollback compatibility evidence is not bound to the exact integrated SHA")
        if rollback["to_migration"] != 8 or rollback["v0_functions_retained"] is not True:
            raise DrillError("V0 rollback compatibility does not cover migration 8")

    def select_backup(self) -> None:
        metadata_path = self.evidence_dir / f"{self.label}.backup-metadata.json"
        self.command(
            "select-latest-valid-backup",
            ["bash", "database/recovery/fetch-backup-metadata.sh", self.label, str(self.evidence_dir)],
            evidence=metadata_path,
            timeout=900,
        )
        generated = self.evidence_dir / f"{self.label}.json"
        generated_checksum = Path(str(generated) + ".sha256")
        if not generated.is_file() or not generated_checksum.is_file():
            raise DrillError("backup metadata reconstruction did not create checksummed evidence")
        generated.rename(metadata_path)
        generated_checksum.rename(Path(str(metadata_path) + ".sha256"))
        self.metadata = load_json(metadata_path)
        validate(BACKUP_SCHEMA, self.metadata, "backup metadata")
        if self.metadata["source"]["gitSha"] != self.git_sha:
            raise DrillError("backup source SHA does not match the exact integrated source")
        if self.metadata["migration"]["currentVersion"] != 8:
            raise DrillError("backup does not contain migration version 8")
        started = parse_time(self.metadata["backup"]["startedAt"])
        archive_end_text = self.backup_status_document["archive"].get("lastArchivedAt")
        if not archive_end_text:
            raise DrillError("WAL archive coverage timestamp is unavailable")
        archive_end = parse_time(archive_end_text)
        if self.pitr_target < started or self.pitr_target > archive_end:
            raise DrillError("reviewed PITR target is outside the proven backup/WAL recovery window")
        self.steps["select-latest-valid-backup"].evidence_ref = evidence_ref(metadata_path)

    def restore(self) -> None:
        self.command(
            "restore-isolated-target",
            ["bash", "database/recovery/prepare-restore-exercise.sh", str(self.target_root), self.target_database],
            evidence=self.target_root / "exercise.json",
            timeout=120,
        )
        self.target_prepared = True
        restore_started = time.monotonic()
        restore_log = self.evidence_dir / "restore-isolated-target.restore.log"
        env = {
            "LIQI_KEEP_RESTORE_RUNNING": "true",
            "LIQI_RESTORE_TARGET_TIME": self.pitr_target.isoformat().replace("+00:00", "Z"),
            "LIQI_BACKUP_STATUS_FILE": str(self.backup_status),
        }
        completed = subprocess.run(
            ["bash", "database/recovery/restore-exercise.sh", self.backup_ref, str(self.target_root), self.target_database],
            cwd=ROOT,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
        )
        elapsed = round((time.monotonic() - restore_started) * 1000)
        self.steps["restore-isolated-target"].duration_ms += elapsed
        self.steps["wal-pitr"].duration_ms += elapsed
        restore_log.write_text(
            f"RETURN_CODE\n{completed.returncode}\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}\n",
            encoding="utf-8",
            newline="\n",
        )
        os.chmod(restore_log, 0o600)
        self.steps["restore-isolated-target"].evidence_ref = evidence_ref(restore_log)
        self.steps["wal-pitr"].evidence_ref = evidence_ref(restore_log)
        if completed.returncode != 0:
            self.steps["restore-isolated-target"].status = "failed"
            self.steps["wal-pitr"].status = "failed"
            raise DrillError(f"isolated restore/PITR failed with exit {completed.returncode}; see {restore_log.name}")
        self.steps["restore-isolated-target"].status = "passed"
        self.steps["wal-pitr"].status = "passed"
        source = self.target_root / "evidence/restore-result.json"
        source_checksum = Path(str(source) + ".sha256")
        if not source.is_file() or not source_checksum.is_file():
            self.steps["restore-isolated-target"].status = "failed"
            self.steps["wal-pitr"].status = "failed"
            raise DrillError("restore verification result is missing")
        destination = self.evidence_dir / "database-restore-result.json"
        shutil.copy2(source, destination)
        shutil.copy2(source_checksum, Path(str(destination) + ".sha256"))
        self.restore_result = load_json(destination)
        validate(RESTORE_SCHEMA, self.restore_result, "database restore result")
        if self.restore_result["success"] is not True:
            self.steps["restore-isolated-target"].status = "failed"
            self.steps["wal-pitr"].status = "failed"
            raise DrillError("database restore verification failed")
        self.steps["restore-isolated-target"].evidence_ref = evidence_ref(destination)
        self.steps["wal-pitr"].evidence_ref = evidence_ref(destination)

    def verify_database(self) -> None:
        def verify() -> None:
            self.command(
                "verify-platform-invariants",
                [
                    "bash",
                    "database/recovery/verify-restore-exercise.sh",
                    str(self.target_root),
                    self.target_database,
                    "8",
                    str(self.recovery_status_path),
                    self.environment,
                ],
                env={
                    "LIQI_BACKUP_STATUS_FILE": str(self.backup_status),
                    "LIQI_BACKUP_STATUS_CHECKSUM_FILE": str(self.backup_status_checksum),
                },
                evidence=self.recovery_status_path,
                timeout=900,
            )
        verify()
        assert self.restore_result is not None
        checks = {item["name"]: item["passed"] for item in self.restore_result["checks"]}
        migration_names = {"migration-version", "migration-manifest-checksum", "migration-row-checksums", "failed-migration-runs"}
        if not all(checks.get(name) is True for name in migration_names):
            self.steps["verify-migrations"].status = "failed"
            raise DrillError("restored migration verification did not pass")
        step = self.steps["verify-migrations"]
        step.status = "passed"
        step.duration_ms = self.steps["verify-platform-invariants"].duration_ms
        step.evidence_ref = evidence_ref(self.evidence_dir / "database-restore-result.json")

    def beam_probe(self) -> None:
        assert self.metadata is not None
        output = self.evidence_dir / "restore-beam-probe.json"
        env = {
            "LIQI_RECOVERY_RELEASE_BIN": str(self.release_bin),
            "LIQI_DATABASE_SOCKET_DIR": str(self.target_root / "run"),
            "LIQI_DATABASE_PORT": os.environ.get("LIQI_RESTORE_PORT", "55432"),
            "LIQI_DATABASE_NAME": "liqi",
            "LIQI_DATABASE_API_PASSWORD_FILE": str(self.api_password_path),
            "LIQI_SOURCE_GIT_SHA": self.git_sha,
            "LIQI_RELEASE_ID": self.args.release_id,
            "LIQI_RESTORE_PROBE_ID": self.metadata["probe"]["probeId"],
            "LIQI_RESTORE_PROBE_EVENT_ID": self.metadata["probe"]["eventId"],
            "LIQI_RUNTIME_CONFIG_PATH": str(self.runtime_config),
        }
        self.command(
            "elixir-read-only-probe",
            ["bash", "beam/bin/database-restore-probe", "--output", str(output)],
            env=env,
            evidence=output,
            timeout=300,
        )
        self.beam_result = load_json(output)
        validate(BEAM_SCHEMA, self.beam_result, "BEAM restore probe")
        if self.beam_result["git_sha"] != self.git_sha or self.beam_result["release_id"] != self.args.release_id:
            raise DrillError("BEAM restore probe is not bound to the exact release")

    def cleanup(self) -> None:
        step = self.steps["cleanup"]
        started = time.monotonic()
        log_path = self.evidence_dir / "cleanup.log"
        if not self.target_prepared:
            log_path.write_text("target was not prepared; no cleanup mutation was required\n", encoding="utf-8", newline="\n")
            os.chmod(log_path, 0o600)
            step.duration_ms = round((time.monotonic() - started) * 1000)
            step.status = "passed"
            step.evidence_ref = evidence_ref(log_path)
            return
        completed = subprocess.run(
            ["bash", "database/recovery/cleanup-restore-exercise.sh", str(self.target_root), self.target_database],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        step.duration_ms = round((time.monotonic() - started) * 1000)
        log_path.write_text(
            f"RETURN_CODE\n{completed.returncode}\nSTDOUT\n{completed.stdout}\nSTDERR\n{completed.stderr}\n",
            encoding="utf-8",
            newline="\n",
        )
        os.chmod(log_path, 0o600)
        step.evidence_ref = evidence_ref(log_path)
        if completed.returncode != 0 or self.target_root.exists():
            step.status = "failed"
            raise DrillError(f"isolated restore cleanup failed; see {log_path.name}")
        step.status = "passed"

    def result(self, status: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        metadata = self.metadata or {
            "backup": {"label": self.label, "startedAt": self.started_at, "stoppedAt": self.started_at, "archiveStop": "unknown"},
            "source": {"gitSha": self.git_sha},
            "migration": {"currentVersion": 0},
        }
        restore = self.restore_result or {"workingTargets": {"rpoObservedSeconds": 0, "rtoObservedSeconds": 0}, "finishedAt": self.started_at}
        backup_created = parse_time(metadata["backup"]["stoppedAt"])
        restore_finished = parse_time(restore.get("finishedAt", self.started_at))
        compatibility_passed = status == "passed" and self.beam_result is not None
        document = {
            "schema_version": "recovery-result-v1",
            "evidence_mode": "live",
            "git_sha": self.git_sha,
            "release_id": self.args.release_id,
            "environment": self.environment,
            "exercise_id": self.exercise_id,
            "started_at": self.started_at,
            "completed_at": utc_now(),
            "status": status,
            "approval_ref": self.args.approval_ref,
            "source": {
                "database_id": self.source_database_id,
                "schema_version": f"migration-{metadata['migration']['currentVersion']}",
                "release_id": self.args.release_id,
                "mutated": False,
            },
            "target": {"database_id": self.target_database, "isolated": True, "publicly_reachable": False},
            "backup": {
                "backup_id": metadata["backup"]["label"],
                "created_at": metadata["backup"]["stoppedAt"],
                "sha256": sha256(self.evidence_dir / f"{self.label}.backup-metadata.json") if (self.evidence_dir / f"{self.label}.backup-metadata.json").is_file() else "0" * 64,
                "wal_end": metadata["backup"].get("archiveStop") or "not-recorded",
                "repository_ref": self.backup_ref,
            },
            "objectives": {"max_rpo_seconds": 300, "max_rto_seconds": 3600},
            "observed": {
                "rpo_seconds": int(restore["workingTargets"].get("rpoObservedSeconds", 0)),
                "rto_seconds": int(restore["workingTargets"].get("rtoObservedSeconds", 0)),
                "backup_age_seconds": max(0, int((now - backup_created).total_seconds())),
                "restore_freshness_seconds": max(0, int((now - restore_finished).total_seconds())),
            },
            "steps": [self.steps[name].document() for name in self.steps],
            "compatibility": {
                "release_schema_compatible": compatibility_passed,
                "migrations_expand_compatible": compatibility_passed,
                "v0_rollback_readable": compatibility_passed,
            },
            "mutations": {
                "isolated_target_mutated": self.target_prepared,
                "source_database_mutated": False,
                "production_traffic_changed": False,
                "oci_mutated": False,
            },
            "cleanup": {
                "required": True,
                "status": self.steps["cleanup"].status,
                "evidence_ref": self.steps["cleanup"].evidence_ref,
            },
            "failures": self.failures,
        }
        validate(RECOVERY_SCHEMA, document, "recovery result")
        return document

    def run(self) -> int:
        status = "failed"
        try:
            self.validate_rollback()
            self.select_backup()
            self.restore()
            self.verify_database()
            self.beam_probe()
            status = "passed"
        except (DrillError, subprocess.SubprocessError, OSError, ValueError) as exc:
            self.failures.append(safe_message(exc))
        finally:
            try:
                self.cleanup()
            except (DrillError, subprocess.SubprocessError, OSError) as exc:
                self.failures.append(safe_message(exc))
                status = "failed"
        if any(step.status != "passed" for step in self.steps.values()):
            status = "failed"
        document = self.result(status)
        write_json_atomic(self.output, document)
        print(f"V1 isolated recovery drill: {status} -> {self.output}")
        return 0 if status == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        return Drill(parse_args()).run()
    except (DrillError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"ERROR recovery-provider: {safe_message(exc)}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
