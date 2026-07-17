#!/usr/bin/env python3
"""Create and verify LIQI PostgreSQL V0 backup/restore evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts/platform"
BACKUP_SCHEMA = CONTRACTS / "database-backup-metadata-v0.schema.json"
BACKUP_EXAMPLE = CONTRACTS / "database-backup-metadata-v0.example.json"
BACKUP_STATUS_SCHEMA = CONTRACTS / "database-backup-status-v0.schema.json"
BACKUP_STATUS_EXAMPLE = CONTRACTS / "database-backup-status-v0.example.json"
RESTORE_SCHEMA = CONTRACTS / "database-restore-result-v0.schema.json"
RESTORE_EXAMPLE = CONTRACTS / "database-restore-result-v0.example.json"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def validate(instance: Any, schema_path: Path) -> None:
    schema = load_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    if errors:
        rendered = "; ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"{schema_path.name} validation failed: {rendered}")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json_with_checksum(output: Path, value: Any) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    checksum_path = Path(str(output) + ".sha256")
    token = uuid.uuid4().hex
    temporary_output = output.with_name(f".{output.name}.{token}.tmp")
    temporary_checksum = checksum_path.with_name(f".{checksum_path.name}.{token}.tmp")
    try:
        with temporary_output.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        checksum_encoded = f"{sha256_bytes(encoded)}  {output.name}\n".encode("utf-8")
        with temporary_checksum.open("wb") as stream:
            stream.write(checksum_encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_output, output)
        os.replace(temporary_checksum, checksum_path)
    finally:
        temporary_output.unlink(missing_ok=True)
        temporary_checksum.unlink(missing_ok=True)
    return checksum_path


def expected_checksum(checksum_path: Path) -> str:
    parts = checksum_path.read_text(encoding="utf-8").strip().split()
    if not parts or len(parts[0]) != 64:
        raise ValueError(f"invalid SHA-256 sidecar: {checksum_path}")
    return parts[0].lower()


def validate_metadata(metadata_path: Path, checksum_path: Path) -> dict[str, Any]:
    expected = expected_checksum(checksum_path)
    actual = sha256_file(metadata_path)
    if expected != actual:
        raise ValueError(f"backup metadata checksum mismatch: expected={expected} actual={actual}")
    metadata = load_json(metadata_path)
    validate(metadata, BACKUP_SCHEMA)
    return metadata


def timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise ValueError(f"invalid timestamp: {value!r}")


def manifest_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        checksum, filename = line.split(maxsplit=1)
        filename = filename.lstrip("*")
        version_text, remainder = filename.split("_", 1)
        entries.append({
            "version": int(version_text),
            "name": remainder.removesuffix(".sql"),
            "checksumSha256": checksum,
        })
    return entries


def select_backup(info: Any, stanza_name: str, run_id: str, backup_type: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(info, list):
        raise ValueError("pgBackRest info must be a JSON array")
    stanza = next((item for item in info if item.get("name") == stanza_name), None)
    if stanza is None:
        raise ValueError(f"pgBackRest stanza not found: {stanza_name}")
    candidates = [
        backup for backup in stanza.get("backup", [])
        if backup.get("type") == backup_type
        and backup.get("annotation", {}).get("liqi-run-id") == run_id
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one pgBackRest backup for run {run_id}, found {len(candidates)}")
    return stanza, candidates[0]


def command_validate_contracts(_: argparse.Namespace) -> int:
    for schema, example in [(BACKUP_SCHEMA, BACKUP_EXAMPLE), (BACKUP_STATUS_SCHEMA, BACKUP_STATUS_EXAMPLE), (RESTORE_SCHEMA, RESTORE_EXAMPLE)]:
        validate(load_json(example), schema)
        print(f"{example.name}: valid")
    return 0


def command_create_metadata(args: argparse.Namespace) -> int:
    info_path = Path(args.info)
    info_raw = info_path.read_bytes()
    info = json.loads(info_raw)
    state = load_json(Path(args.database_state))
    manifest = Path(args.manifest)
    stanza, backup = select_backup(info, "liqi", args.run_id, args.backup_type)
    annotation = backup.get("annotation", {})
    probe = state.get("probe")
    if not isinstance(probe, dict):
        raise ValueError("database state does not contain a completed recovery probe")
    manifest_sha = sha256_file(manifest)
    expected_annotations = {
        "liqi-run-id": args.run_id,
        "liqi-migration-version": str(state["migrationVersion"]),
        "liqi-probe-event-id": str(probe["eventId"]),
        "liqi-manifest-sha256": manifest_sha,
    }
    for key, expected in expected_annotations.items():
        if annotation.get(key) != expected:
            raise ValueError(f"pgBackRest annotation mismatch for {key}: {annotation.get(key)!r} != {expected!r}")
    if state.get("failedMigrationRuns") != 0:
        raise ValueError("backup refused because failed migration runs are present")
    if probe.get("probeStatus") != "completed" or probe.get("outboxState") != "succeeded" or probe.get("effectCount") != 1:
        raise ValueError("backup recovery probe invariant is not terminal and unique")

    pgbackrest_version = backup.get("backrest", {}).get("version") or args.pgbackrest_version
    if not pgbackrest_version:
        raise ValueError("pgBackRest version missing from info and command argument")
    backup_archive = backup.get("archive") or {}
    backup_timestamps = backup.get("timestamp") or {}
    metadata = {
        "$schema": "./database-backup-metadata-v0.schema.json",
        "schemaVersion": "database-backup-metadata-v0",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "backupRunId": args.run_id,
        "stanza": "liqi",
        "source": {"database": state["database"], "authority": "postgresql", "hostRef": args.host_ref},
        "repository": {
            "type": "oci-object-storage-s3-compatible",
            "bucket": args.bucket,
            "namespace": args.namespace,
            "region": args.region,
            "path": "/postgresql/v0",
            "uriStyle": "path",
            "clientSideEncryption": stanza.get("cipher") or "aes-256-cbc",
            "dedicated": True,
        },
        "backup": {
            "label": backup["label"],
            "type": backup["type"],
            "startedAt": timestamp(backup_timestamps["start"]),
            "stoppedAt": timestamp(backup_timestamps["stop"]),
            "archiveStart": backup_archive.get("start"),
            "archiveStop": backup_archive.get("stop"),
            "annotation": expected_annotations,
            "infoSha256": sha256_bytes(info_raw),
        },
        "postgresql": {"major": state["postgresqlMajor"], "version": state["postgresqlVersion"]},
        "migration": {"currentVersion": state["migrationVersion"], "manifestSha256": manifest_sha},
        "probe": probe,
        "pgBackRest": {"version": pgbackrest_version, "repositoryFormat": "pgbackrest-encrypted-s3-v0"},
        "recoveryTargets": {"workingRpo": "5m", "workingRto": "60m", "pitr": True, "restoreScope": "entire-postgresql-cluster"},
        "costClassification": {"class": "always-free-safe-with-v0-cap", "v0ObjectBudgetGiB": 18},
    }
    validate(metadata, BACKUP_SCHEMA)
    output = Path(args.output)
    checksum = write_json_with_checksum(output, metadata)
    print(json.dumps({
        "backupLabel": metadata["backup"]["label"],
        "metadata": str(output),
        "checksum": str(checksum),
        "passed": True,
    }, separators=(",", ":")))
    return 0


def command_validate_metadata(args: argparse.Namespace) -> int:
    metadata = validate_metadata(Path(args.metadata), Path(args.checksum))
    print(json.dumps({
        "validation": "database-backup-metadata-v0",
        "backupLabel": metadata["backup"]["label"],
        "passed": True,
    }, separators=(",", ":")))
    return 0


def validate_checksummed_document(document_path: Path, checksum_path: Path, schema_path: Path) -> dict[str, Any]:
    expected = expected_checksum(checksum_path)
    actual = sha256_file(document_path)
    if expected != actual:
        raise ValueError(f"document checksum mismatch: expected={expected} actual={actual}")
    document = load_json(document_path)
    validate(document, schema_path)
    return document


def command_write_backup_status(args: argparse.Namespace) -> int:
    value = load_json(Path(args.input))
    validate(value, BACKUP_STATUS_SCHEMA)
    checksum = write_json_with_checksum(Path(args.output), value)
    print(json.dumps({"backupStatus": args.output, "checksum": str(checksum), "passed": True}, separators=(",", ":")))
    return 0


def command_validate_backup_status(args: argparse.Namespace) -> int:
    value = validate_checksummed_document(Path(args.status), Path(args.checksum), BACKUP_STATUS_SCHEMA)
    print(json.dumps({
        "validation": "database-backup-status-v0",
        "recoveryReady": value["recoveryReady"],
        "passed": True,
    }, separators=(",", ":")))
    return 0


def command_validate_restore_result(args: argparse.Namespace) -> int:
    result_path = Path(args.result)
    result = validate_checksummed_document(result_path, Path(args.checksum), RESTORE_SCHEMA)
    print(json.dumps({
        "validation": "database-restore-result-v0",
        "restoreId": result["restoreId"],
        "success": result["success"],
        "passed": True,
    }, separators=(",", ":")))
    return 0


def add_check(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any, detail: str = "") -> None:
    check: dict[str, Any] = {"name": name, "passed": expected == actual, "expected": expected, "actual": actual}
    if detail:
        check["detail"] = detail
    checks.append(check)


def command_verify_restore(args: argparse.Namespace) -> int:
    metadata_path = Path(args.metadata)
    checksum_path = Path(args.checksum)
    metadata = validate_metadata(metadata_path, checksum_path)
    actual = load_json(Path(args.actual))
    manifest = Path(args.manifest)
    local_manifest_sha = sha256_file(manifest)
    expected_rows = manifest_entries(manifest)
    actual_rows = actual.get("migrations", [])
    checks: list[dict[str, Any]] = []
    add_check(checks, "backup-metadata-checksum", expected_checksum(checksum_path), sha256_file(metadata_path))
    add_check(checks, "backup-metadata-schema", "database-backup-metadata-v0", metadata.get("schemaVersion"))
    add_check(checks, "postgres-major", metadata["postgresql"]["major"], actual.get("postgresqlMajor"))
    add_check(checks, "migration-version", metadata["migration"]["currentVersion"], actual.get("migrationVersion"))
    add_check(checks, "migration-manifest-checksum", metadata["migration"]["manifestSha256"], local_manifest_sha)
    add_check(checks, "migration-row-checksums", expected_rows, actual_rows)
    add_check(checks, "probe-state", "completed", actual.get("probe", {}).get("probeStatus"))
    add_check(checks, "probe-outbox-terminal-state", "succeeded", actual.get("probe", {}).get("outboxState"))
    add_check(checks, "probe-terminal-effect", 1, actual.get("probe", {}).get("effectCount"))
    add_check(checks, "failed-migration-runs", 0, actual.get("failedMigrationRuns"))
    add_check(checks, "probe-identity", metadata["probe"]["probeId"], actual.get("probe", {}).get("probeId"))
    add_check(checks, "probe-event-identity", metadata["probe"]["eventId"], actual.get("probe", {}).get("eventId"))
    add_check(checks, "recovery-promoted", False, actual.get("inRecovery"))
    add_check(checks, "restore-archive-disabled", "off", actual.get("archiveMode"))
    add_check(checks, "restore-network-isolated", "", actual.get("listenAddresses"))

    started = datetime.fromisoformat(args.started_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    finished = datetime.now(timezone.utc)
    duration = max(0.0, (finished - started).total_seconds())
    backup_stopped = datetime.fromisoformat(metadata["backup"]["stoppedAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
    probe_completed = datetime.fromisoformat(metadata["probe"]["completedAt"].replace("Z", "+00:00")).astimezone(timezone.utc)
    rpo_observed_seconds = max(0, math.ceil((backup_stopped - probe_completed).total_seconds()))
    rto_observed_seconds = max(0, math.ceil(duration))
    success = all(check["passed"] for check in checks)
    result = {
        "$schema": "./database-restore-result-v0.schema.json",
        "schemaVersion": "database-restore-result-v0",
        "restoreId": args.restore_id,
        "startedAt": started.isoformat().replace("+00:00", "Z"),
        "finishedAt": finished.isoformat().replace("+00:00", "Z"),
        "durationSeconds": round(duration, 3),
        "backupLabel": metadata["backup"]["label"],
        "backupMetadataSha256": sha256_file(metadata_path),
        "target": {
            "database": "liqi",
            "pgData": args.target_pgdata,
            "socketDirectory": args.socket_directory,
            "port": args.port,
            "isolated": True,
            "archiveMode": "off",
        },
        "success": success,
        "checks": checks,
        "workingTargets": {
            "rpo": "5m",
            "rto": "60m",
            "rpoObservedSeconds": rpo_observed_seconds,
            "rtoObservedSeconds": rto_observed_seconds,
            "rtoMet": rto_observed_seconds <= 3600,
        },
        "operatorAction": [] if success else [
            "Do not promote or route traffic to the restored cluster.",
            "Inspect failed checks, repair the recovery path, and repeat the isolated restore drill.",
        ],
    }
    validate(result, RESTORE_SCHEMA)
    write_json_with_checksum(Path(args.output), result)
    print(json.dumps({"validation": "database-restore-result-v0", "success": success, "output": args.output}, separators=(",", ":")))
    return 0 if success else 1


def command_create_recovery_status(args: argparse.Namespace) -> int:
    latest_metadata_path = Path(args.metadata)
    latest_metadata = validate_metadata(latest_metadata_path, Path(args.metadata_checksum))
    restore_path = Path(args.restore_result)
    restore = validate_checksummed_document(restore_path, Path(args.restore_checksum), RESTORE_SCHEMA)
    backup_status = validate_checksummed_document(
        Path(args.backup_status), Path(args.backup_status_checksum), BACKUP_STATUS_SCHEMA
    )

    restore_source_path = Path(args.restore_source_metadata) if args.restore_source_metadata else (
        latest_metadata_path.parent / f"{restore['backupLabel']}.json"
    )
    restore_source_checksum_path = (
        Path(args.restore_source_metadata_checksum)
        if args.restore_source_metadata_checksum
        else Path(str(restore_source_path) + ".sha256")
    )
    restore_source_metadata = validate_metadata(restore_source_path, restore_source_checksum_path)

    if args.environment not in {"development", "staging", "production"}:
        raise ValueError(f"invalid environment: {args.environment}")
    if not backup_status.get("recoveryReady") or backup_status.get("reasons"):
        raise ValueError(f"current backup/archive status is not recovery-ready: {backup_status.get('reasons')}")
    if not restore.get("success"):
        raise ValueError("restore verification did not pass")

    latest_label = latest_metadata["backup"]["label"]
    latest = backup_status.get("latestBackup")
    if not isinstance(latest, dict) or latest.get("label") != latest_label:
        raise ValueError("current backup status does not identify latest backup metadata")
    if restore.get("backupLabel") != restore_source_metadata["backup"]["label"]:
        raise ValueError("restore result references a different source backup label")
    if restore.get("backupMetadataSha256") != sha256_file(restore_source_path):
        raise ValueError("restore result references a different source backup metadata checksum")

    current_migration = latest_metadata["migration"]["currentVersion"]
    if backup_status.get("migrationVersion") != current_migration:
        raise ValueError("current migration version differs from latest backup metadata")
    if restore_source_metadata["migration"]["currentVersion"] != current_migration:
        raise ValueError("restore proof predates the current migration version")
    if restore_source_metadata["postgresql"]["major"] != latest_metadata["postgresql"]["major"]:
        raise ValueError("restore source PostgreSQL major differs from current backup")

    current_probe = backup_status.get("probe")
    expected_probe = latest_metadata["probe"]
    if not isinstance(current_probe, dict):
        raise ValueError("current backup status probe is missing")
    for key in ("probeId", "eventId", "probeStatus", "outboxState", "effectCount"):
        if current_probe.get(key) != expected_probe.get(key):
            raise ValueError(f"current backup status probe differs for {key}")

    archive = backup_status.get("archive")
    if not isinstance(archive, dict) or not archive.get("lastArchivedAt"):
        raise ValueError("current WAL archive status is incomplete")
    archive_lag = archive.get("secondsSinceLastArchive")
    if not isinstance(archive_lag, (int, float)) or archive_lag < 0:
        raise ValueError("current WAL archive lag is unavailable")

    latest_backup_ref = args.backup_evidence_ref or (
        f"oci://{latest_metadata['repository']['namespace']}/{latest_metadata['repository']['bucket']}"
        f"{latest_metadata['repository']['path']}/metadata/{latest_label}.json"
    )
    restore_source_ref = args.restore_source_evidence_ref or (
        f"oci://{restore_source_metadata['repository']['namespace']}/{restore_source_metadata['repository']['bucket']}"
        f"{restore_source_metadata['repository']['path']}/metadata/{restore_source_metadata['backup']['label']}.json"
    )
    restore_ref = args.restore_evidence_ref or f"file://{restore_path.resolve()}"
    status = {
        "schema_version": "recovery-status-v0",
        "owner": "Senior 2",
        "environment": args.environment,
        "database": {
            "authority_version": f"database-v0-postgresql-{latest_metadata['postgresql']['major']}",
            "migration_version": str(current_migration),
        },
        "backup": {
            "completed_at": latest_metadata["backup"]["stoppedAt"],
            "off_host": True,
            "encrypted": latest_metadata["repository"]["clientSideEncryption"] == "aes-256-cbc",
            "evidence_ref": latest_backup_ref,
        },
        "wal_archive": {
            "last_archived_at": archive["lastArchivedAt"],
            "lag_seconds": math.ceil(float(archive_lag)),
            "evidence_ref": args.wal_evidence_ref,
        },
        "restore_verification": {
            "status": "passed",
            "verified_at": restore["finishedAt"],
            "rpo_observed_seconds": restore["workingTargets"]["rpoObservedSeconds"],
            "rto_observed_seconds": restore["workingTargets"]["rtoObservedSeconds"],
            "source_backup_ref": restore_source_ref,
            "evidence_ref": restore_ref,
        },
    }
    operations_schema = ROOT / "contracts/operations/recovery-status-v0.schema.json"
    if operations_schema.exists():
        validate(status, operations_schema)
    encoded = json.dumps(status, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8", newline="\n")
    print(encoded, end="")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    validate_contracts = sub.add_parser("validate-contracts")
    validate_contracts.set_defaults(func=command_validate_contracts)

    create = sub.add_parser("create-metadata")
    create.add_argument("--info", required=True)
    create.add_argument("--database-state", required=True)
    create.add_argument("--manifest", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--backup-type", choices=("full", "diff"), required=True)
    create.add_argument("--bucket", required=True)
    create.add_argument("--namespace", required=True)
    create.add_argument("--region", required=True)
    create.add_argument("--host-ref", required=True)
    create.add_argument("--pgbackrest-version")
    create.add_argument("--output", required=True)
    create.set_defaults(func=command_create_metadata)

    verify_metadata = sub.add_parser("validate-metadata")
    verify_metadata.add_argument("--metadata", required=True)
    verify_metadata.add_argument("--checksum", required=True)
    verify_metadata.set_defaults(func=command_validate_metadata)

    write_status = sub.add_parser("write-backup-status")
    write_status.add_argument("--input", required=True)
    write_status.add_argument("--output", required=True)
    write_status.set_defaults(func=command_write_backup_status)

    validate_status = sub.add_parser("validate-backup-status")
    validate_status.add_argument("--status", required=True)
    validate_status.add_argument("--checksum", required=True)
    validate_status.set_defaults(func=command_validate_backup_status)

    validate_result = sub.add_parser("validate-restore-result")
    validate_result.add_argument("--result", required=True)
    validate_result.add_argument("--checksum", required=True)
    validate_result.set_defaults(func=command_validate_restore_result)

    recovery_status = sub.add_parser("create-recovery-status")
    recovery_status.add_argument("--environment", required=True)
    recovery_status.add_argument("--metadata", required=True)
    recovery_status.add_argument("--metadata-checksum", required=True)
    recovery_status.add_argument("--restore-result", required=True)
    recovery_status.add_argument("--restore-checksum", required=True)
    recovery_status.add_argument("--restore-source-metadata")
    recovery_status.add_argument("--restore-source-metadata-checksum")
    recovery_status.add_argument("--backup-status", required=True)
    recovery_status.add_argument("--backup-status-checksum", required=True)
    recovery_status.add_argument("--backup-evidence-ref")
    recovery_status.add_argument("--restore-evidence-ref")
    recovery_status.add_argument("--restore-source-evidence-ref")
    recovery_status.add_argument("--wal-evidence-ref", default="database://pg_stat_archiver")
    recovery_status.add_argument("--output")
    recovery_status.set_defaults(func=command_create_recovery_status)

    restore = sub.add_parser("verify-restore")
    restore.add_argument("--metadata", required=True)
    restore.add_argument("--checksum", required=True)
    restore.add_argument("--actual", required=True)
    restore.add_argument("--manifest", required=True)
    restore.add_argument("--restore-id", required=True)
    restore.add_argument("--started-at", required=True)
    restore.add_argument("--target-pgdata", required=True)
    restore.add_argument("--socket-directory", required=True)
    restore.add_argument("--port", type=int, required=True)
    restore.add_argument("--output", required=True)
    restore.set_defaults(func=command_verify_restore)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if hasattr(args, "run_id"):
            uuid.UUID(args.run_id)
        if hasattr(args, "restore_id"):
            uuid.UUID(args.restore_id)
        return args.func(args)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": "database-recovery-contract", "message": str(exc), "passed": False}, separators=(",", ":")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
