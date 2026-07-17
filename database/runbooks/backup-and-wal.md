# PostgreSQL backup and WAL archive runbook

## Cost and topology classification

- pgBackRest software and source changes: no OCI cost.
- dedicated OCI Object Storage repository: `always-free-safe-with-v0-cap` only while total V0 Object Storage remains within the declared 18 GiB budget and tenancy entitlements remain valid.
- Customer Secret Keys are required by the OCI S3 Compatibility API; they are credentials, not source configuration.
- This runbook never treats local boot/block-volume copies as durable backup.

## Configure pgBackRest

Senior 1 supplies concrete paths and Object Storage outputs:

```bash
LIQI_PGDATA=/var/lib/liqi/postgresql/data \
LIQI_PGBACKREST_SPOOL_PATH=/var/lib/liqi/postgresql/backup-staging/pgbackrest-spool \
LIQI_PGBACKREST_LOG_PATH=/var/log/liqi/postgresql/pgbackrest \
LIQI_OCI_OBJECT_NAMESPACE='<namespace>' \
LIQI_OCI_REGION='ap-singapore-2' \
LIQI_DATABASE_BACKUP_BUCKET='<approved-bucket>' \
LIQI_PGBACKREST_CONFIG_PATH=/etc/pgbackrest/pgbackrest.conf \
  database/bin/render-pgbackrest-config.sh
```

Expected: one root/postgres-readable config with path-style OCI endpoint, client-side AES-256-CBC, retention 2 full/7 differential, 2 GiB archive-push queue and no key/passphrase values.

## Initialize and check repository

These commands mutate the approved backup repository and require explicit project-owner authorization:

```bash
database/bin/pgbackrest-command.sh --stanza=liqi stanza-create
database/bin/pgbackrest-command.sh --stanza=liqi check
```

Expected: stanza status `ok` and a WAL segment reaches Object Storage.

## Capacity preflight

The backup command first runs a read-only, fail-closed guard:

```bash
database/bin/backup-capacity-check.sh
```

It reads OCI `approximateSize`, PostgreSQL `pg_database_size`, adds a 1 GiB peak safety margin and rejects the backup when the dedicated bucket would exceed 18 GiB or the V0 database exceeds 8 GiB. `null`/unknown Object Storage usage also rejects the backup. Do not bypass the guard; approve PAYG/capacity or safely expire a separately restored backup first.

## Full and differential backup

```bash
database/bin/backup.sh full
database/bin/backup.sh diff
```

A successful command must:

1. create a completed recovery probe;
2. run pgBackRest with archive checking;
3. generate metadata and SHA-256 sidecar;
4. publish metadata first and the non-overwritable SHA-256 sidecar last as the completion marker through OCI instance-principal authentication;
5. emit JSON with `durableMetadataPublished=true`.

## Recovery status

```bash
database/bin/backup-status.sh
database/bin/postgres-metrics.sh
database/bin/backup-metrics.sh
LIQI_RESTORE_RESULT_FILE=/var/lib/liqi/postgresql/backup-staging/restore/latest/restore-result.json database/bin/restore-result-metrics.sh
```

`recoveryReady=false` does not automatically make PostgreSQL data authority unavailable, but it invalidates the 5-minute RPO claim and must alert Senior 4. `liqi_database_restore_verification_success` comes only from a checksummed restore result; it is never inferred from repository health.

Publish the exact Senior 4 recovery seam only when current backup/archive status and checksummed restore evidence agree:

```bash
LIQI_ENVIRONMENT=development \
LIQI_BACKUP_METADATA_FILE=/var/lib/liqi/postgresql/backup-staging/metadata/<label>.json \
LIQI_RESTORE_RESULT_FILE=/var/lib/liqi/postgresql/backup-staging/restore/latest/restore-result.json \
LIQI_RECOVERY_STATUS_OUTPUT=/var/lib/liqi/postgresql/backup-staging/metadata/recovery-status-v0.json \
  database/bin/recovery-status.sh
```

Expected: a `recovery-status-v0` document owned by Senior 2. The command fails closed for missing checksums, stale/different backup identity, incomplete WAL status, failed restore verification or migration mismatch. When Senior 4's schema is present after integration, the provider validates against it directly.

## Archive queue overflow

If pgBackRest reports that `archive-push-queue-max=2GiB` was exceeded, queued WAL may have been dropped:

1. declare recovery readiness failed;
2. restore Object Storage connectivity/capacity;
3. run `pgbackrest check`;
4. take a new full backup;
5. execute an isolated restore drill;
6. only then restore the 5-minute RPO claim.

Do not silently increase the queue or consume remaining host disk. A larger queue requires a capacity decision.

## Secret rotation

Keep the old S3 key and cipher passphrase available until a backup made with the new material has passed restore verification and every backup encrypted by the old passphrase has expired. Never rotate by deleting the only readable key first.
