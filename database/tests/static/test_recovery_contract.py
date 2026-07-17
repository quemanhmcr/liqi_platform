#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/recovery_contract.py"
MANIFEST = ROOT / "migrations/manifest.sha256"


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True, check=False)
    if result.returncode != expected:
        raise AssertionError(f"command returned {result.returncode}, expected {expected}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def manifest_rows() -> list[dict[str, object]]:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        checksum, filename = line.split(maxsplit=1)
        filename = filename.lstrip("*")
        version, name = filename.split("_", 1)
        rows.append({"version": int(version), "name": name.removesuffix(".sql"), "checksumSha256": checksum})
    return rows


def main() -> int:
    run("validate-contracts")
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        info = json.loads((ROOT / "fixtures/pgbackrest-info-v0.json").read_text(encoding="utf-8"))
        manifest_sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
        info[0]["backup"][0]["annotation"]["liqi-manifest-sha256"] = manifest_sha
        info_path = temp / "info.json"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        metadata = temp / "metadata.json"
        run(
            "create-metadata",
            "--info", str(info_path),
            "--database-state", str(ROOT / "fixtures/database-backup-state-v0.json"),
            "--manifest", str(MANIFEST),
            "--run-id", "10000000-0000-4000-8000-000000000001",
            "--backup-type", "full",
            "--bucket", "liqi-database-backup-v0",
            "--namespace", "example-namespace",
            "--region", "ap-singapore-2",
            "--host-ref", "oci-host-v0://host/database-authority",
            "--output", str(metadata),
        )
        checksum = Path(str(metadata) + ".sha256")
        run("validate-metadata", "--metadata", str(metadata), "--checksum", str(checksum))
        meta = json.loads(metadata.read_text(encoding="utf-8"))
        source_metadata = temp / f"{meta['backup']['label']}.json"
        source_metadata.write_bytes(metadata.read_bytes())
        source_checksum = Path(str(source_metadata) + ".sha256")
        source_checksum.write_text(
            f"{hashlib.sha256(source_metadata.read_bytes()).hexdigest()}  {source_metadata.name}\n",
            encoding="utf-8",
        )
        actual = {
            "postgresqlMajor": 17,
            "postgresqlVersion": "17.10",
            "migrationVersion": 4,
            "failedMigrationRuns": 0,
            "inRecovery": False,
            "archiveMode": "off",
            "listenAddresses": "",
            "migrations": manifest_rows(),
            "probe": {
                "probeId": meta["probe"]["probeId"],
                "eventId": meta["probe"]["eventId"],
                "probeStatus": "completed",
                "outboxState": "succeeded",
                "effectCount": 1,
            },
        }
        actual_path = temp / "actual.json"
        actual_path.write_text(json.dumps(actual), encoding="utf-8")
        result_path = temp / "restore-result.json"
        run(
            "verify-restore",
            "--metadata", str(metadata),
            "--checksum", str(checksum),
            "--actual", str(actual_path),
            "--manifest", str(MANIFEST),
            "--restore-id", str(uuid.uuid4()),
            "--started-at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "--target-pgdata", "/var/lib/liqi/postgresql/backup-staging/restore/test/data",
            "--socket-directory", "/run/liqi/restore/test",
            "--port", "55432",
            "--output", str(result_path),
        )
        result_checksum = Path(str(result_path) + ".sha256")
        run("validate-restore-result", "--result", str(result_path), "--checksum", str(result_checksum))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result["success"]:
            raise AssertionError("valid restore evidence was rejected")
        if result["workingTargets"]["rpoObservedSeconds"] > 300:
            raise AssertionError("recovery probe did not demonstrate the working RPO")

        backup_status_input = temp / "backup-status-input.json"
        backup_status_input.write_text(json.dumps({
            "schemaVersion": "database-backup-status-v0",
            "recoveryReady": True,
            "reasons": [],
            "latestBackup": {
                "label": meta["backup"]["label"],
                "type": meta["backup"]["type"],
                "ageSeconds": 120,
            },
            "archive": {
                "archivedCount": 4,
                "failedCount": 0,
                "lastArchivedWal": meta["backup"]["archiveStop"],
                "lastArchivedAt": meta["backup"]["stoppedAt"],
                "lastFailedWal": None,
                "lastFailedAt": None,
                "secondsSinceLastArchive": 120,
            },
            "migrationVersion": meta["migration"]["currentVersion"],
            "probe": meta["probe"],
            "observedAt": meta["generatedAt"],
        }), encoding="utf-8")
        backup_status_path = temp / "backup-status.json"
        run("write-backup-status", "--input", str(backup_status_input), "--output", str(backup_status_path))
        backup_status_checksum = Path(str(backup_status_path) + ".sha256")
        run("validate-backup-status", "--status", str(backup_status_path), "--checksum", str(backup_status_checksum))
        recovery_status_path = temp / "recovery-status.json"
        run(
            "create-recovery-status",
            "--environment", "development",
            "--metadata", str(metadata),
            "--metadata-checksum", str(checksum),
            "--restore-result", str(result_path),
            "--restore-checksum", str(result_checksum),
            "--backup-status", str(backup_status_path),
            "--backup-status-checksum", str(backup_status_checksum),
            "--output", str(recovery_status_path),
        )
        recovery_status = json.loads(recovery_status_path.read_text(encoding="utf-8"))
        expected_keys = {
            "schema_version", "owner", "environment", "database", "backup",
            "wal_archive", "restore_verification",
        }
        if set(recovery_status) != expected_keys:
            raise AssertionError("Senior 4 recovery status has an unexpected top-level shape")
        if recovery_status["schema_version"] != "recovery-status-v0" or recovery_status["owner"] != "Senior 2":
            raise AssertionError("Senior 4 recovery status ownership/version is incorrect")
        if recovery_status["restore_verification"]["rpo_observed_seconds"] != result["workingTargets"]["rpoObservedSeconds"]:
            raise AssertionError("recovery status lost observed RPO semantics")

        # A newer backup may establish freshness while restore proof remains tied to
        # an older retained backup, as long as database major and migration semantics match.
        latest_meta = json.loads(metadata.read_text(encoding="utf-8"))
        latest_meta["backupRunId"] = "30000000-0000-4000-8000-000000000001"
        latest_meta["generatedAt"] = "2026-07-18T03:05:00Z"
        latest_meta["backup"]["label"] = "20260717-030000F_20260718-030000D"
        latest_meta["backup"]["type"] = "diff"
        latest_meta["backup"]["startedAt"] = "2026-07-18T03:00:00Z"
        latest_meta["backup"]["stoppedAt"] = "2026-07-18T03:04:30Z"
        latest_meta["probe"] = {
            "probeId": "30000000-0000-4000-8000-000000000002",
            "eventId": "30000000-0000-4000-8000-000000000003",
            "probeStatus": "completed",
            "outboxState": "succeeded",
            "effectCount": 1,
            "completedAt": "2026-07-18T02:59:59Z",
        }
        latest_metadata = temp / "latest.json"
        latest_metadata.write_text(json.dumps(latest_meta, indent=2) + "\n", encoding="utf-8")
        latest_checksum = Path(str(latest_metadata) + ".sha256")
        latest_checksum.write_text(
            f"{hashlib.sha256(latest_metadata.read_bytes()).hexdigest()}  {latest_metadata.name}\n",
            encoding="utf-8",
        )
        newer_status_input = temp / "newer-backup-status-input.json"
        newer_status = json.loads(backup_status_input.read_text(encoding="utf-8"))
        newer_status["latestBackup"] = {
            "label": latest_meta["backup"]["label"],
            "type": "diff",
            "ageSeconds": 60,
        }
        newer_status["probe"] = latest_meta["probe"]
        newer_status["observedAt"] = latest_meta["generatedAt"]
        newer_status_input.write_text(json.dumps(newer_status), encoding="utf-8")
        newer_status_path = temp / "newer-backup-status.json"
        run("write-backup-status", "--input", str(newer_status_input), "--output", str(newer_status_path))
        newer_status_checksum = Path(str(newer_status_path) + ".sha256")
        newer_recovery_status_path = temp / "newer-recovery-status.json"
        run(
            "create-recovery-status",
            "--environment", "development",
            "--metadata", str(latest_metadata),
            "--metadata-checksum", str(latest_checksum),
            "--restore-result", str(result_path),
            "--restore-checksum", str(result_checksum),
            "--restore-source-metadata", str(source_metadata),
            "--restore-source-metadata-checksum", str(source_checksum),
            "--backup-status", str(newer_status_path),
            "--backup-status-checksum", str(newer_status_checksum),
            "--output", str(newer_recovery_status_path),
        )
        newer_recovery_status = json.loads(newer_recovery_status_path.read_text(encoding="utf-8"))
        if latest_meta["backup"]["label"] not in newer_recovery_status["backup"]["evidence_ref"]:
            raise AssertionError("latest backup freshness evidence was not used")
        if meta["backup"]["label"] not in newer_recovery_status["restore_verification"]["source_backup_ref"]:
            raise AssertionError("restore proof did not preserve its source backup")

        # Keep later negative checks on the newer current backup status.
        backup_status_path = newer_status_path
        backup_status_checksum = newer_status_checksum

        actual["migrationVersion"] = 2
        actual_path.write_text(json.dumps(actual), encoding="utf-8")
        failed_result = temp / "failed-result.json"
        run(
            "verify-restore",
            "--metadata", str(metadata),
            "--checksum", str(checksum),
            "--actual", str(actual_path),
            "--manifest", str(MANIFEST),
            "--restore-id", str(uuid.uuid4()),
            "--started-at", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "--target-pgdata", "/var/lib/liqi/postgresql/backup-staging/restore/test/data",
            "--socket-directory", "/run/liqi/restore/test",
            "--port", "55432",
            "--output", str(failed_result),
            expected=1,
        )
        if json.loads(failed_result.read_text(encoding="utf-8"))["success"]:
            raise AssertionError("migration mismatch was not detected")
        run(
            "validate-restore-result",
            "--result", str(failed_result),
            "--checksum", str(failed_result) + ".sha256",
        )
        run(
            "create-recovery-status",
            "--environment", "development",
            "--metadata", str(metadata),
            "--metadata-checksum", str(checksum),
            "--restore-result", str(failed_result),
            "--restore-checksum", str(failed_result) + ".sha256",
            "--backup-status", str(backup_status_path),
            "--backup-status-checksum", str(backup_status_checksum),
            expected=1,
        )

        corrupted_result = temp / "corrupted-result.json"
        corrupted_result.write_bytes(result_path.read_bytes() + b" ")
        run(
            "validate-restore-result",
            "--result", str(corrupted_result),
            "--checksum", str(result_checksum),
            expected=1,
        )

        corrupted = temp / "corrupted.json"
        corrupted.write_bytes(metadata.read_bytes() + b" ")
        run("validate-metadata", "--metadata", str(corrupted), "--checksum", str(checksum), expected=1)

    print(json.dumps({"validation": "database-recovery-contract-v0", "passed": True}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
