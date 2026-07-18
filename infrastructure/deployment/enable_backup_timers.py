#!/usr/bin/env python3
"""Validate real backup/restore proof before optionally enabling provider-owned backup timers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOTS = (ROOT / "contracts/platform", Path("/usr/local/lib/liqi-database/contracts/platform"))
TIMERS = (
    "liqi-database-backup-full.timer",
    "liqi-database-backup-diff.timer",
    "liqi-database-repository-check.timer",
)


def contract(name: str) -> Path:
    for root in CONTRACT_ROOTS:
        path = root / name
        if path.is_file():
            return path
    raise FileNotFoundError(name)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksum(document: Path, checksum: Path) -> None:
    fields = checksum.read_text(encoding="utf-8").strip().split()
    if not fields or fields[0] != digest(document):
        raise RuntimeError(f"checksum mismatch: {document}")


def validate(schema_name: str, document: Any) -> None:
    schema = load(contract(schema_name))
    failures = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if failures:
        raise RuntimeError(f"{schema_name}: {failures[0].message}")


def run(*argv: str) -> None:
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {argv}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-status", required=True, type=Path)
    parser.add_argument("--backup-status-checksum", required=True, type=Path)
    parser.add_argument("--restore-result", required=True, type=Path)
    parser.add_argument("--restore-result-checksum", required=True, type=Path)
    parser.add_argument("--approval-reference")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    verify_checksum(args.backup_status, args.backup_status_checksum)
    verify_checksum(args.restore_result, args.restore_result_checksum)
    backup = load(args.backup_status)
    restore = load(args.restore_result)
    validate("database-backup-status-v0.schema.json", backup)
    validate("database-restore-result-v0.schema.json", restore)

    if backup["recoveryReady"] is not True or backup["reasons"]:
        raise SystemExit("backup status is not recovery ready")
    if restore["success"] is not True or restore["target"]["isolated"] is not True:
        raise SystemExit("isolated restore proof did not pass")
    if restore["target"]["archiveMode"] != "off" or not all(item["passed"] for item in restore["checks"]):
        raise SystemExit("restore proof has failed checks or unsafe archive mode")
    if restore["workingTargets"]["rtoMet"] is not True:
        raise SystemExit("restore proof did not meet the declared RTO")
    if restore["backupLabel"] != backup["latestBackup"]["label"]:
        raise SystemExit("restore proof does not reference the current latest backup")

    if args.execute and (os.name != "posix" or os.geteuid() != 0):
        raise SystemExit("timer mutation requires root on POSIX")
    if args.execute and (not args.approval_reference or len(args.approval_reference.strip()) < 3):
        raise SystemExit("timer mutation requires approval reference")

    status = "validated"
    if args.execute:
        for timer in TIMERS:
            run("systemctl", "enable", "--now", timer)
        status = "enabled"

    result = {
        "schema_version": "liqi.infrastructure.backup-timer-enable-result/v1",
        "status": status,
        "backup_label": backup["latestBackup"]["label"],
        "backup_status_sha256": digest(args.backup_status),
        "restore_result_sha256": digest(args.restore_result),
        "restore_id": restore["restoreId"],
        "rpo_observed_seconds": restore["workingTargets"]["rpoObservedSeconds"],
        "rto_observed_seconds": restore["workingTargets"]["rtoObservedSeconds"],
        "timers": list(TIMERS),
        "approval_reference": args.approval_reference if args.execute else None,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mutation_performed": args.execute,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
