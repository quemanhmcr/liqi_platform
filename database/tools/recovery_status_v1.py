#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/database/recovery-status-v1.schema.json"
BACKUP_SCHEMA = ROOT / "contracts/platform/database-backup-status-v0.schema.json"
RESTORE_SCHEMA = ROOT / "contracts/platform/database-restore-result-v0.schema.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_document(value: dict[str, Any], schema_path: Path, label: str) -> None:
    schema = load(schema_path)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.path),
    )
    if errors:
        rendered = "; ".join(
            f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"invalid {label} document: {rendered}")


def verify(path: Path, checksum: Path, schema_path: Path, label: str) -> dict[str, Any]:
    tokens = checksum.read_text(encoding="utf-8").strip().split()
    if not tokens or len(tokens[0]) != 64:
        raise ValueError(f"invalid checksum sidecar: {checksum}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != tokens[0].lower():
        raise ValueError(f"checksum mismatch: {path}")
    value = load(path)
    validate_document(value, schema_path, label)
    return value


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-state", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--backup-status")
    parser.add_argument("--backup-status-checksum")
    parser.add_argument("--restore-result")
    parser.add_argument("--restore-result-checksum")
    parser.add_argument("--restored-source-revision")
    parser.add_argument("--output")
    args = parser.parse_args()

    if len(args.source_revision) != 40 or any(ch not in "0123456789abcdef" for ch in args.source_revision):
        raise SystemExit("source revision must be a lowercase 40-character Git SHA")

    now = datetime.now(timezone.utc)
    state = load(Path(args.database_state))
    migration_version = int(state.get("migrationVersion", 0))

    backup_status = "blocked"
    latest_backup_at = None
    latest_wal_at = None
    freshness = None
    metadata_verified = False
    if args.backup_status and args.backup_status_checksum:
        backup = verify(Path(args.backup_status), Path(args.backup_status_checksum), BACKUP_SCHEMA, "backup status")
        metadata_verified = True
        observed = parse_time(backup["observedAt"])
        age = int(backup["latestBackup"]["ageSeconds"])
        latest_backup_at = observed - timedelta(seconds=age)
        latest_wal_at = parse_time(backup["archive"]["lastArchivedAt"])
        freshness = int(backup["archive"]["secondsSinceLastArchive"])
        backup_status = "passed" if (
            backup.get("recoveryReady") is True
            and int(backup.get("migrationVersion", 0)) >= 8
            and int(backup["archive"].get("failedCount", 0)) == 0
            and freshness <= 300
        ) else "failed"

    restore_status = "blocked"
    restore_verified_at = None
    restore_duration = None
    restored_migration = None
    invariants_passed = False
    restored_revision = args.restored_source_revision
    if args.restore_result and args.restore_result_checksum:
        restore = verify(Path(args.restore_result), Path(args.restore_result_checksum), RESTORE_SCHEMA, "restore result")
        restore_verified_at = parse_time(restore["finishedAt"])
        restore_duration = int(round(float(restore["durationSeconds"])))
        checks = restore.get("checks", [])
        invariants_passed = bool(restore.get("success")) and all(check.get("passed") for check in checks)
        migration_check = next((check for check in checks if check.get("name") == "migration-version"), None)
        try:
            restored_migration = int(migration_check["actual"]) if migration_check else None
        except (TypeError, ValueError):
            restored_migration = None
        restore_status = "passed" if (
            invariants_passed
            and restored_migration is not None
            and restored_migration >= 8
            and restore_duration <= 3600
            and restored_revision is not None
        ) else "failed"

    overall = "passed" if (
        migration_version >= 8
        and backup_status == "passed"
        and restore_status == "passed"
        and restored_revision == args.source_revision
    ) else ("failed" if "failed" in {backup_status, restore_status} else "blocked")

    value = {
        "$schema": "./recovery-status-v1.schema.json",
        "schemaVersion": "recovery-status-v1",
        "status": overall,
        "sourceRevision": args.source_revision,
        "releaseId": args.release_id,
        "migrationVersion": migration_version,
        "requiredMigrationVersion": 8,
        "backup": {
            "status": backup_status,
            "repositoryFormat": "pgbackrest-encrypted-posix-tls-v1",
            "latestBackupAt": iso(latest_backup_at),
            "latestWalArchivedAt": iso(latest_wal_at),
            "metadataChecksumVerified": metadata_verified,
            "freshnessSeconds": freshness,
        },
        "restore": {
            "status": restore_status,
            "verifiedAt": iso(restore_verified_at),
            "durationSeconds": restore_duration,
            "restoredSourceRevision": restored_revision,
            "restoredMigrationVersion": restored_migration,
            "invariantsPassed": invariants_passed,
        },
        "targets": {"rpoSeconds": 300, "rtoSeconds": 3600},
        "observedAt": iso(now),
    }

    schema = load(SCHEMA)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.path),
    )
    if errors:
        raise SystemExit("; ".join(error.message for error in errors))

    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8", newline="\n")
    print(json.dumps(value, separators=(",", ":")))
    return 0 if overall == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
