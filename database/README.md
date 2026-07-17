# PostgreSQL Authority V0

This directory owns the LIQI V0 PostgreSQL authority, least-privilege roles, forward-only migration lifecycle, PgBouncer policy, transactional outbox foundation, encrypted backup/WAL archive and restore verification.

It intentionally contains no LIQI product/domain schema.

## Provider contracts

- Authority: `contracts/platform/database-v0.schema.json`
- Accepted V0 values: `contracts/platform/database-v0.example.json`
- Backup evidence: `contracts/platform/database-backup-metadata-v0.schema.json`
- Restore evidence: `contracts/platform/database-restore-result-v0.schema.json`
- Decisions: `docs/adr/0200-*`, `0201-*`, `0202-*`

Validate every source contract without starting PostgreSQL, building an image or mutating OCI:

```bash
database/tests/run-source-validation.sh
```

## Runtime boundary for Senior 3

Runtime processes connect only through PgBouncer transaction pooling. They must not depend on session state, temporary tables, session advisory locks, `LISTEN/NOTIFY` through the pool, or named prepared statements unless the selected driver proves compatibility.

Approved persistence functions after migration version 3:

- Producer: `platform.enqueue_outbox_v0(...)`
- Walking skeleton producer: `platform.request_probe_v0(...)`
- Worker claim: `platform.claim_outbox_v0(...)`
- Worker acknowledgement: `platform.ack_outbox_v0(...)`
- Worker retry/dead letter: `platform.fail_outbox_v0(...)`
- Probe idempotent effect: `platform.apply_probe_effect_and_ack_v0(...)`
- Readiness: `platform.database_readiness_v0(required_version)`

The wire adapter must preserve event ID, type, version, occurred-at, aggregate key, ordering key and payload. `database/tests/contract/validate_wire_mapping.py` consumes Senior 3's accepted example; the placeholder fixture is not a wire contract and states its removal condition.

## Cluster lifecycle

Production bootstrap uses the local administrative Unix socket:

```bash
database/bin/bootstrap-cluster.sh
```

Forward-only migration uses standard libpq environment variables and a secret-backed `PGPASSFILE` when password authentication is required:

```bash
PGHOST=/run/postgresql \
PGDATABASE=liqi \
PGUSER=liqi_migrator \
PGPASSFILE=/run/liqi/secrets/database/migrator-pgpass \
  database/bin/migrate.sh
```

No script accepts a password or plaintext DSN argument. Runtime and backup credentials are materialized outside Git by the Senior 1 host contract.

## Provider commands for Senior 4

- Direct liveness: `database/bin/liveness.sh`
- PgBouncer-backed readiness: `database/bin/readiness.sh`
- PostgreSQL/outbox/WAL metrics: `database/bin/postgres-metrics.sh`
- PgBouncer pool metrics: `database/bin/pgbouncer-metrics.sh`
- Backup status and age: `database/bin/backup-status.sh`, `database/bin/backup-metrics.sh`
- Last checksummed restore evidence metrics: `database/bin/restore-result-metrics.sh`
- Senior 4 recovery contract document: `database/bin/recovery-status.sh`
- Recovery execution and verification: `operations/disaster-recovery/database/*.sh`

Backup readiness and restore verification are intentionally separate. A healthy repository does not prove a backup has restored successfully, and a historical restore result does not prove current WAL archiving is healthy.

## Integration and recovery validation

See `database/tests/README.md` and `operations/runbooks/database/`. The local Windows authoring workspace has no PostgreSQL or pgBackRest binaries, so real database integration, repository creation, backup and isolated restore remain mandatory gates on the target PostgreSQL 17 host. None has been simulated as production evidence.
