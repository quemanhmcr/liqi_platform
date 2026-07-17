# PostgreSQL isolated restore drill

## Objective

Prove that a selected encrypted pgBackRest backup and its WAL can restore to a new PostgreSQL 17 data directory within the 60-minute working RTO and preserve migration/probe invariants.

## Fetch metadata

Read-only OCI operation:

```bash
database/recovery/fetch-backup-metadata.sh \
  '<pgBackRest-label>' \
  /var/lib/liqi/postgresql/backup-staging/restore/evidence
```

Expected: metadata schema/checksum validation succeeds. A missing, malformed or mismatched sidecar blocks restore.

## Provider-owned exercise lifecycle

Use the operations runner as the generic entrypoint. Dry-run is non-mutating and proves that all provider commands exist:

```bash
python scripts/operations/run_recovery_exercise.py \
  --plan operations/disaster-recovery/recovery-exercise-plan-v0.example.json \
  --output .artifacts/recovery-exercise-plan.json \
  --evidence-dir .artifacts/recovery-exercise-evidence
```

Expected: `status=planned`, four commands under `database/recovery/**`, and zero OCI mutation.

Execution requires a reviewed plan containing a non-null `approval_ref`, the exact same `--approval-ref`, current checksummed backup/WAL status evidence, and a disposable isolated target. The provider lifecycle is:

```bash
database/recovery/prepare-restore-exercise.sh \
  /var/lib/liqi/recovery-exercises/manual-drill \
  liqi_restore_manual_drill

database/recovery/restore-exercise.sh \
  "oci://<namespace>/<bucket>/postgresql/v0/metadata/<pgBackRest-label>.json" \
  /var/lib/liqi/recovery-exercises/manual-drill \
  liqi_restore_manual_drill

LIQI_BACKUP_STATUS_FILE=/var/lib/liqi/postgresql/backup-staging/metadata/backup-status-v0.json \
  database/recovery/verify-restore-exercise.sh \
    /var/lib/liqi/recovery-exercises/manual-drill \
    liqi_restore_manual_drill \
    4 \
    /var/lib/liqi/recovery-exercises/manual-drill/evidence/recovery-status.json \
    development

database/recovery/cleanup-restore-exercise.sh \
  /var/lib/liqi/recovery-exercises/manual-drill \
  liqi_restore_manual_drill
```

For point-in-time recovery, set `LIQI_RESTORE_TARGET_TIME` to an explicit UTC timestamp before the restore step. The restore command fetches metadata read-only through OCI instance-principal authentication, restores below the approved isolated root, verifies migration/probe invariants, stops the restored cluster, and writes checksummed provider evidence. The verify command emits `recovery-status-v0`; cleanup always runs through the operations state machine.

Expected machine-readable restore result:

- `success=true`;
- PostgreSQL major 17;
- current migration version 4 and each stored checksum equal source manifest;
- no failed migration run;
- metadata probe completed;
- outbox state `succeeded`;
- exactly one terminal probe effect;
- `inRecovery=false`, `archive_mode=off`, empty `listen_addresses`;
- `workingTargets.rtoMet=true`.

## Failure handling

- Do not promote or route traffic based on process exit alone.
- Preserve result JSON, checksum and PostgreSQL restore log.
- Classify failure as repository access, WAL gap, PostgreSQL startup, migration mismatch, probe mismatch or RTO miss.
- Test another retained backup only after recording why the selected backup failed.
- A successful different backup does not erase evidence that one retained backup is invalid.
