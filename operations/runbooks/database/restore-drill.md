# PostgreSQL isolated restore drill

## Objective

Prove that a selected encrypted pgBackRest backup and its WAL can restore to a new PostgreSQL 17 data directory within the 60-minute working RTO and preserve migration/probe invariants.

## Fetch metadata

Read-only OCI operation:

```bash
operations/disaster-recovery/database/fetch-backup-metadata.sh \
  '<pgBackRest-label>' \
  /var/lib/liqi/postgresql/backup-staging/restore/evidence
```

Expected: metadata schema/checksum validation succeeds. A missing, malformed or mismatched sidecar blocks restore.

## Restore

Choose an empty target below the approved restore root. Never use production `PGDATA`.

```bash
LIQI_RESTORE_METADATA_FILE=/var/lib/liqi/postgresql/backup-staging/restore/evidence/<label>.json \
LIQI_RESTORE_METADATA_CHECKSUM_FILE=/var/lib/liqi/postgresql/backup-staging/restore/evidence/<label>.json.sha256 \
LIQI_RESTORE_ROOT=/var/lib/liqi/postgresql/backup-staging/restore \
LIQI_RESTORE_TARGET_PGDATA=/var/lib/liqi/postgresql/backup-staging/restore/drill-$(date -u +%Y%m%dT%H%M%SZ)/data \
LIQI_RESTORE_PORT=55432 \
  operations/disaster-recovery/database/restore.sh
```

For point-in-time recovery, additionally set an explicit UTC value:

```bash
LIQI_RESTORE_TARGET_TIME='2026-07-17 03:02:00+00'
```

Expected machine-readable result:

- `success=true`;
- PostgreSQL major 17;
- current migration version and each stored checksum equal source manifest;
- no failed migration run;
- metadata probe completed;
- outbox state `succeeded`;
- exactly one terminal probe effect;
- `inRecovery=false`, `archive_mode=off`, empty `listen_addresses`;
- `workingTargets.rtoMet=true`.

The restore cluster is stopped after verification unless `LIQI_KEEP_RESTORE_RUNNING=true` is explicitly set for investigation. It must never receive application traffic.

## Failure handling

- Do not promote or route traffic based on process exit alone.
- Preserve result JSON, checksum and PostgreSQL restore log.
- Classify failure as repository access, WAL gap, PostgreSQL startup, migration mismatch, probe mismatch or RTO miss.
- Test another retained backup only after recording why the selected backup failed.
- A successful different backup does not erase evidence that one retained backup is invalid.
