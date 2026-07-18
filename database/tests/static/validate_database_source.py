#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
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
if versions and versions[-1] != 8:
    failures.append(f"V1 required migration must be 8, observed {versions[-1]}")

v0_migration_digests = {
    "000000000001_platform_metadata.sql": "2c7bc57a430bff46c6aaaa48be3932e4bdc6a6ffb2c20c78743009609318cc27",
    "000000000002_platform_outbox_probe.sql": "3e698ff70de80eeb346844c2f580e30be02e699dc9aad7064028fc74be18e744",
    "000000000003_recovery_probe_and_backup_state.sql": "b8483f6df37c945ba606740c858a51970648f00f308b2e7eb6c3e8267a7e4a98",
    "000000000004_runtime_persistence_handoffs.sql": "33fc45325a689d382e2311dcdb2c0476a6ee7927d2b038788ccfc648d90d7699",
}
for name, expected_digest in v0_migration_digests.items():
    path = MIGRATIONS / name
    observed_digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
    if observed_digest != expected_digest:
        failures.append(f"immutable V0 migration changed: {name}")

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

v1_migration_requirements = {
    "000000000005_v1_command_idempotency_and_envelope.sql": (
        "platform.command_idempotency_v1",
        "request_fingerprint text NOT NULL",
        "request_fingerprint ~ '^[0-9a-f]{64}$'",
        "platform.enqueue_outbox_v1",
        "protocol_version smallint NOT NULL DEFAULT 0",
        "octet_length(payload::text) <= 65536",
    ),
    "000000000006_v1_probe_and_realtime_handoff.sql": (
        "platform.request_probe_v1",
        "requested_request_fingerprint text",
        "event.actor_key AS aggregate_key",
        "event.payload_type AS event_type",
        "platform.claim_outbox_v1",
        "platform.apply_probe_effect_and_ack_v1",
        "platform.read_realtime_handoff_v1",
        "ERRCODE = 'LQ001'",
        "ERRCODE = 'LQ002'",
        "ERRCODE = 'LQ003'",
        "ERRCODE = 'LQ004'",
        "FOR UPDATE SKIP LOCKED",
    ),
    "000000000007_oban_postgresql_v14.sql": (
        "CREATE TABLE oban.oban_jobs",
        "CREATE UNLOGGED TABLE oban.oban_peers",
        "CONSTRAINT attempt_range",
        "ADD CONSTRAINT non_negative_priority CHECK (priority >= 0) NOT VALID",
        "CREATE INDEX oban_jobs_args_index",
        "CREATE INDEX oban_jobs_meta_index",
        "CREATE INDEX oban_jobs_state_cancelled_at_index",
        "CREATE INDEX oban_jobs_state_discarded_at_index",
        "CREATE INDEX oban_jobs_state_queue_priority_scheduled_at_id_index",
        "COMMENT ON TABLE oban.oban_jobs IS",
        "'14'",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON oban.oban_jobs, oban.oban_peers TO liqi_worker",
    ),
    "000000000008_v1_readiness_retention_and_recovery.sql": (
        "platform.database_readiness_v1",
        "platform.current_oban_migration_version_v1",
        "platform.prune_command_idempotency_v1",
        "platform.prune_outbox_v1",
        "platform.backup_verification_state_v1",
        "requested_batch_size > 500",
    ),
}
for migration_name, required_tokens in v1_migration_requirements.items():
    migration_text = (MIGRATIONS / migration_name).read_text(encoding="utf-8")
    for token in required_tokens:
        if token not in migration_text:
            failures.append(f"{migration_name} missing V1 seam: {token}")

oban_migration = (MIGRATIONS / "000000000007_oban_postgresql_v14.sql").read_text(encoding="utf-8")
for forbidden in ("oban_jobs_notify", "CREATE TRIGGER oban_notify"):
    if forbidden in oban_migration:
        failures.append(f"Oban migration 14 retained removed notifier object: {forbidden}")

all_migration_text = "\n".join(path.read_text(encoding="utf-8") for path in files)
if all_migration_text.count("CREATE TABLE platform.outbox_events") != 1:
    failures.append("platform.outbox_events must remain the single outbox authority")
if "GRANT SELECT ON platform.outbox_events TO liqi_api" in all_migration_text:
    failures.append("API runtime must not receive direct outbox table authority access")
if "GRANT SELECT ON platform.command_idempotency_v1 TO liqi_api" in all_migration_text:
    failures.append("API runtime must not receive direct idempotency table access")

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

restore_script = (ROOT / "recovery/restore.sh").read_text(encoding="utf-8")
for required_restore_guard in (
    "in-place restore is forbidden",
    "restore target must be below LIQI_RESTORE_ROOT",
    "--archive-mode=off",
    "--target-action=promote",
    "listen_addresses = ''",
):
    if required_restore_guard not in restore_script:
        failures.append(f"missing restore safety guard: {required_restore_guard}")

shell_scripts = sorted(ROOT.rglob("*.sh"))
for path in shell_scripts:
    relative = path.relative_to(ROOT.parent).as_posix()
    stage = subprocess.check_output(
        ["git", "ls-files", "--stage", relative],
        cwd=ROOT.parent,
        text=True,
    ).strip()
    mode = stage.split(maxsplit=1)[0] if stage else ""
    if mode != "100755":
        failures.append(
            f"database shell command must be executable in Git: {relative} mode={mode or 'missing'}"
        )

recovery_dir = ROOT / "recovery"
for command in (
    "fetch-backup-metadata.sh",
    "prepare-restore-exercise.sh",
    "restore-exercise.sh",
    "restore.sh",
    "verify-restore-exercise.sh",
    "verify-restore.sh",
    "cleanup-restore-exercise.sh",
):
    path = recovery_dir / command
    if not path.is_file():
        failures.append(f"provider recovery command missing: database/recovery/{command}")
        continue
    stage = subprocess.check_output(
        ["git", "ls-files", "--stage", f"database/recovery/{command}"],
        cwd=ROOT.parent,
        text=True,
    ).strip()
    mode = stage.split(maxsplit=1)[0] if stage else ""
    if mode != "100755":
        failures.append(f"provider recovery command must be executable in Git: database/recovery/{command} mode={mode or 'missing'}")

for command, required_tokens in {
    "prepare-restore-exercise.sh": ("target root already exists and is not empty", "production_traffic"),
    "restore-exercise.sh": ("fetch-backup-metadata.sh", "LIQI_RESTORE_TARGET_PGDATA", "restore.sh"),
    "verify-restore-exercise.sh": ("required migration", "recovery-status.sh", "restore-source-metadata"),
    "cleanup-restore-exercise.sh": ("refusing cleanup", "rm -rf --one-file-system"),
}.items():
    command_text = (recovery_dir / command).read_text(encoding="utf-8")
    for token in required_tokens:
        if token not in command_text:
            failures.append(f"database/recovery/{command} missing lifecycle guard: {token}")

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
        r"(?m)^SELECT\s+(?:ok|is|isnt|lives_ok|throws_ok|has_role|isnt_super|isnt_superuser|has_schema|schema_owner_is|hasnt_schema_privilege|hasnt_table_privilege|has_function_privilege|hasnt_function_privilege)\s*\(",
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
    "validation": "database-source-v1",
    "migrations": len(files),
    "latestVersion": max(versions),
    "pgbouncerMode": "transaction",
    "plaintextSecrets": False,
    "passed": True,
}, separators=(",", ":")))
