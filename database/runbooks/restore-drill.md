# PostgreSQL V1 isolated restore drill

## Objective

Prove that a selected encrypted pgBackRest backup and WAL chain restore to a new PostgreSQL 17 directory within 60 minutes while preserving migration 8, manifest checksums and recovery-probe/outbox invariants.

## Read-only metadata reconstruction

```bash
database/recovery/fetch-backup-metadata.sh \
  '<pgBackRest-label>' \
  /var/lib/liqi/recovery-exercises/manual-drill/metadata
```

The command queries the independent repository, reconstructs `database-backup-metadata-v1` from durable annotations, writes a SHA-256 sidecar and validates both. A missing annotation, repository mismatch or checksum failure blocks restore.

## V1 readiness provider command

The promotion/readiness entry point is `database/bin/run-restore-drill-v1.sh`. It owns orchestration but delegates restore, database invariant verification and the BEAM read-only probe to their provider commands. The final evidence is written outside the disposable target and validated against `contracts/readiness/recovery-result-v1.schema.json`.

Materialize every input in a protected operator shell; do not place credentials, OCIDs or resolved secret values in Git or command logs:

```bash
export LIQI_RECOVERY_ENVIRONMENT=production
export LIQI_RECOVERY_BACKUP_REF='pgbackrest://management/database-backup-repository/liqi/<pgBackRest-label>'
export LIQI_RECOVERY_TARGET_ROOT='/var/lib/liqi/recovery-exercises/<exercise-id>'
export LIQI_RECOVERY_TARGET_DATABASE='liqi_restore_<exercise_id>'
export LIQI_RECOVERY_SOURCE_DATABASE_ID='liqi-live'
export LIQI_RESTORE_TARGET_TIME='<reviewed UTC PITR timestamp>'
export LIQI_RECOVERY_RELEASE_BIN='/opt/liqi/current/bin/liqi_platform'
export LIQI_RUNTIME_CONFIG_PATH='/etc/liqi/runtime/current.json'
export LIQI_DATABASE_API_PASSWORD_FILE='/run/liqi/secrets/database/api-password'
export LIQI_BACKUP_STATUS_FILE='/var/lib/liqi/postgresql/backup-staging/metadata/backup-status-v0.json'
export LIQI_BACKUP_STATUS_CHECKSUM_FILE="$LIQI_BACKUP_STATUS_FILE.sha256"
export LIQI_V0_ROLLBACK_COMPATIBILITY='/secure/evidence/v0-rollback-compatibility-v1.json'

database/bin/run-restore-drill-v1.sh \
  --release-id '<exact liqi-v1 release ID>' \
  --approval-ref 'V1-SELFHOSTED-CONTROL-OCI-LIVE-20260718' \
  --output '/secure/evidence/recovery-result-v1.json'
```

The command fails closed unless the backup metadata, WAL coverage, source SHA, migration 8, V0 compatibility evidence and release ID all agree. It leaves the isolated PostgreSQL process running only long enough for `LiqiPersistence.RestoreProbe` to execute through the signed release against the Unix socket with the least-privilege `liqi_api` role. Cleanup runs in `finally`; a cleanup failure makes the result failed. The source database, production traffic and OCI remain unmodified.

## Provider-owned lifecycle primitives

Dry-run the generic operations state machine first; it performs no source database or OCI mutation:

```bash
python scripts/operations/run_recovery_exercise.py \
  --plan operations/disaster-recovery/recovery-exercise-plan-v0.example.json \
  --output .artifacts/recovery-exercise-plan.json \
  --evidence-dir .artifacts/recovery-exercise-evidence
```

Execution requires a reviewed non-null approval reference and a disposable isolated target:

```bash
database/recovery/prepare-restore-exercise.sh \
  /var/lib/liqi/recovery-exercises/manual-drill \
  liqi_restore_manual_drill

database/recovery/restore-exercise.sh \
  "pgbackrest://management/database-backup-repository/liqi/<pgBackRest-label>" \
  /var/lib/liqi/recovery-exercises/manual-drill \
  liqi_restore_manual_drill

LIQI_BACKUP_STATUS_FILE=/var/lib/liqi/postgresql/backup-staging/metadata/backup-status-v0.json \
  database/recovery/verify-restore-exercise.sh \
    /var/lib/liqi/recovery-exercises/manual-drill \
    liqi_restore_manual_drill \
    8 \
    /var/lib/liqi/recovery-exercises/manual-drill/evidence/recovery-status.json \
    production

database/recovery/cleanup-restore-exercise.sh \
  /var/lib/liqi/recovery-exercises/manual-drill \
  liqi_restore_manual_drill
```

For PITR, set `LIQI_RESTORE_TARGET_TIME` to an explicit UTC timestamp before restore. The target remains below the approved recovery root, uses an isolated Unix socket/high port, disables archive mode, never routes production traffic, and is cleaned only through the provider command.

Required evidence: `success=true`, PostgreSQL major 17, migration 8 and exact manifest rows, no failed migration run, the same completed probe/event identity, succeeded outbox state, exactly one terminal effect, `inRecovery=false`, `archive_mode=off`, empty `listen_addresses`, and `workingTargets.rtoMet=true`.

A different successful backup does not erase a failed retained recovery point. Preserve logs/evidence, classify the gap, and repair or expire it only under the retention policy.
