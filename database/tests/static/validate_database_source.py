#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from pglast import parse_sql

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MANIFEST = MIGRATIONS / "manifest.sha256"

failures: list[str] = []
files = sorted(MIGRATIONS.glob("[0-9]???????????_*.sql"))
if not files:
    failures.append("no migrations found")

name_re = re.compile(r"^[0-9]{12}_[a-z0-9_]+\.sql$")
versions: list[int] = []
for path in files:
    if not name_re.fullmatch(path.name):
        failures.append(f"invalid migration name: {path.name}")
    versions.append(int(path.name.split("_", 1)[0]))
    sql = path.read_text(encoding="utf-8")
    try:
        parse_sql(sql)
    except Exception as exc:
        failures.append(f"PostgreSQL parser rejected {path.name}: {exc}")
    if re.search(r"\b(DROP\s+(TABLE|SCHEMA|COLUMN)|ALTER\s+TABLE\s+.+\s+RENAME\s+)\b", sql, re.IGNORECASE):
        failures.append(f"destructive SQL requires decision note: {path.name}")
    if "PASSWORD" in sql.upper() or "postgresql://" in sql.lower():
        failures.append(f"secret-shaped SQL found: {path.name}")

if versions != sorted(set(versions)):
    failures.append("migration versions are not unique and ordered")

manifest_entries: dict[str, str] = {}
for line in MANIFEST.read_text(encoding="utf-8").splitlines() if MANIFEST.exists() else []:
    checksum, name = line.split(maxsplit=1)
    manifest_entries[name.lstrip("*")] = checksum

for path in files:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if manifest_entries.get(path.name) != digest:
        failures.append(f"manifest checksum mismatch: {path.name}")
if set(manifest_entries) != {path.name for path in files}:
    failures.append("manifest file set differs from migration directory")

config = (ROOT / "config/pgbouncer.ini").read_text(encoding="utf-8")
required_pgbouncer = {
    "pool_mode = transaction",
    "max_client_conn = 300",
    "max_db_connections = 40",
    "liqi_api = pool_size=20 max_user_connections=20",
    "liqi_realtime = pool_size=5 max_user_connections=5",
    "liqi_worker = pool_size=10 max_user_connections=10",
    "liqi_readonly = pool_size=3 max_user_connections=3",
    "liqi_monitor = pool_mode=session pool_size=2 max_user_connections=2",
    "listen_addr = 127.0.0.1",
    "auth_type = scram-sha-256",
}
for line in required_pgbouncer:
    if line not in config:
        failures.append(f"missing PgBouncer contract line: {line}")

migration4 = (MIGRATIONS / "000000000004_runtime_persistence_handoffs.sql").read_text(encoding="utf-8")
for seam in (
    "platform.publish_realtime_handoff_v0",
    "platform.read_realtime_handoff_v0",
    "platform.observe_probe_v0",
    "GRANT EXECUTE ON FUNCTION platform.read_realtime_handoff_v0(bigint, integer) TO liqi_realtime",
    "REVOKE SELECT ON platform.probe_state_v0, platform.probe_effects_v0 FROM liqi_readonly",
):
    if seam not in migration4:
        failures.append(f"migration 4 missing provider seam: {seam}")

postgres_config = (ROOT / "config/postgresql-liqi.conf").read_text(encoding="utf-8")
for line in ["listen_addresses = ''", "max_connections = 80", "archive_timeout = '5min'", "password_encryption = 'scram-sha-256'"]:
    if line not in postgres_config:
        failures.append(f"missing PostgreSQL contract line: {line}")

text_suffixes = {".sql", ".sh", ".py", ".conf", ".ini", ".json", ".md", ".sha256", ".template", ".service", ".timer"}

pgbackrest_config = (ROOT / "config/pgbackrest.conf.template").read_text(encoding="utf-8")
required_pgbackrest = {
    "repo1-type=s3",
    "repo1-s3-uri-style=path",
    "repo1-storage-verify-tls=y",
    "repo1-cipher-type=aes-256-cbc",
    "archive-async=y",
    "archive-push-queue-max=2GiB",
    "repo1-retention-full=2",
    "repo1-retention-diff=7",
    "cmd=@@PGBACKREST_WRAPPER@@",
}
for line in required_pgbackrest:
    if line not in pgbackrest_config:
        failures.append(f"missing pgBackRest contract line: {line}")
for forbidden in ("repo1-s3-key=", "repo1-s3-key-secret=", "repo1-cipher-pass="):
    if forbidden in pgbackrest_config:
        failures.append(f"persistent pgBackRest template contains secret option: {forbidden}")

if "pgbackrest-command.sh --stanza=liqi archive-push %p" not in postgres_config:
    failures.append("PostgreSQL archive_command does not use the secret-safe pgBackRest boundary")

backup_script = (ROOT / "bin/backup.sh").read_text(encoding="utf-8")
metadata_position = backup_script.find('--name "$metadata_object"')
checksum_position = backup_script.find('--name "$checksum_object"')
if metadata_position < 0 or checksum_position < 0 or metadata_position >= checksum_position:
    failures.append("backup metadata must publish before checksum completion marker")
if backup_script.count("--no-overwrite") != 2 or "--force" in backup_script:
    failures.append("backup metadata and checksum must be append-only OCI uploads")

restore_metrics = (ROOT / "bin/restore-result-metrics.sh").read_text(encoding="utf-8")
for required_metric_guard in (
    "LIQI_RESTORE_RESULT_CHECKSUM_FILE",
    "validate-restore-result",
    "liqi_database_restore_verification_success",
    "liqi_database_restore_duration_seconds",
):
    if required_metric_guard not in restore_metrics:
        failures.append(f"restore-result metrics missing integrity/metric seam: {required_metric_guard}")

restore_script = (ROOT.parent / "operations/disaster-recovery/database/restore.sh").read_text(encoding="utf-8")
for required_restore_guard in (
    "in-place restore is forbidden",
    "restore target must be below LIQI_RESTORE_ROOT",
    "--archive-mode=off",
    "--target-action=promote",
    "listen_addresses = ''",
):
    if required_restore_guard not in restore_script:
        failures.append(f"missing restore safety guard: {required_restore_guard}")

systemd_dir = ROOT / "systemd"
for unit in systemd_dir.glob("*.service"):
    unit_text = unit.read_text(encoding="utf-8")
    for required_unit_line in ("NoNewPrivileges=true", "MemoryMax=", "TasksMax="):
        if required_unit_line not in unit_text:
            failures.append(f"{unit.name} missing bounded service setting: {required_unit_line}")

for path in sorted((ROOT / "tests/pgtap").glob("*.sql")):
    sql = path.read_text(encoding="utf-8")
    plan_match = re.search(r"SELECT plan\((\d+)\)", sql)
    if not plan_match:
        failures.append(f"pgTAP plan missing: {path.name}")
        continue
    assertion_count = len(re.findall(
        r"(?m)^SELECT\s+(?:ok|is|isnt|lives_ok|throws_ok|has_role|isnt_super|has_schema|schema_owner_is|hasnt_schema_privilege|hasnt_table_privilege|has_function_privilege|hasnt_function_privilege)\s*\(",
        sql,
    ))
    if int(plan_match.group(1)) != assertion_count:
        failures.append(f"pgTAP plan mismatch {path.name}: plan={plan_match.group(1)} assertions={assertion_count}")

all_text = "\n".join(
    path.read_text(encoding="utf-8")
    for path in ROOT.rglob("*")
    if path.is_file() and path.suffix in text_suffixes
)
secret_patterns = [
    re.compile(r"postgres(?:ql)?://[^\s]+:[^\s]+@", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
for pattern in secret_patterns:
    if pattern.search(all_text):
        failures.append(f"secret material pattern detected: {pattern.pattern}")

if failures:
    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    raise SystemExit(1)

print(json.dumps({
    "validation": "database-source-v0",
    "migrations": len(files),
    "latestVersion": max(versions),
    "pgbouncerMode": "transaction",
    "plaintextSecrets": False,
    "passed": True,
}, separators=(",", ":")))
