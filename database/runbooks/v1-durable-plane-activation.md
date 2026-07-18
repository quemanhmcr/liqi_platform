# V1 durable plane activation

## Approval boundary

Senior 2 does not execute live OCI mutation. Senior 4 is the only infrastructure mutation operator, and Senior 5 accepts evidence before command traffic is enabled.

The following steps require explicit approval because they mutate the live database, backup repository, deployment, or traffic:

1. apply migration 5-8 to the live PostgreSQL authority
2. start the Elixir release against live PgBouncer
3. write live backup/WAL evidence or perform an isolated restore drill
4. enable a V1 command route or cut traffic back to V0

## Preconditions

- approved `v0-platform-foundation-ready` tag and exact-SHA owner evidence exist
- current backup status is recovery-ready and WAL freshness is at most 300 seconds
- migration manifest checksums pass
- PostgreSQL 17 and Oban migration compatibility are accepted
- PgBouncer is running in transaction mode with the published role caps
- command ownership is route-scoped; V0 and V1 never dual-write the same scope
- release ID and exact 40-character Git SHA are known

## Approved migration command

Working directory: repository root on the live host.

Required environment:

```bash
export PGHOST=/run/postgresql
export PGPORT=5432
export PGDATABASE=liqi
export PGUSER=liqi_migrator
export PGPASSFILE=/run/liqi/secrets/database/migrator-pgpass
```

Command:

```bash
bash database/bin/migrate.sh
```

Mutation: acquires the migration advisory lock, applies unapplied forward-only SQL migrations, and records their checksums in `platform.schema_migrations`. It does not run down migrations.

Expected result: migration version `8`, Oban migration version `14`, no failed migration run, and all existing V0 functions retained.

Required evidence:

```bash
PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=liqi PGUSER=liqi_monitor \
  bash database/bin/readiness-v1.sh
PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=liqi PGUSER=liqi_monitor \
  bash database/bin/durable-work-metrics-v1.sh
```

Send the complete JSON readiness line, migration log from the advisory-lock acquisition through version 8, and durable-work metrics to Senior 5.

## Shadow/read-only start

Senior 4 deploys the exact release through the approved deployment mechanism. Senior 1 starts the runtime with command routes disabled and validates:

- `:persistence_adapter` is `LiqiPersistence.RuntimeAdapter`
- `:liqi_persistence, :repos` points to the single Senior 1-supervised command/realtime/worker Repo set
- provider dependency apps have not started a second Repo or Oban tree
- all three Repo pools connect through PgBouncer
- readiness returns `passed` and `writeReady=true`
- no named prepared/session-state error occurs across repeated transactions
- PgBouncer server connections remain within the 40-slot ceiling
- Oban recovery queue remains paused
- outbox, handoff, and Oban oldest-age metrics remain bounded

## Command activation

Enable only the approved route/capability. Confirm V0 no longer command-writes that scope before V1 is enabled. Observe query latency, pool wait, outbox p95 age, dead letters, Oban age, and reconnect/gap repair.

## Application rollback

Disable the V1 route and re-enable the V0 owner for that scope. Roll back the application release only. Do not run destructive database rollback. Migration 5-8 and the V0-compatible function signatures remain in place during the migration window.

## Recovery evidence

After approved backup and isolated restore operations, compose exact-SHA evidence:

```bash
export LIQI_SOURCE_REVISION=<40-character-git-sha>
export LIQI_RELEASE_ID=<release-id>
export LIQI_BACKUP_STATUS_FILE=<checksummed-backup-status.json>
export LIQI_RESTORE_RESULT_FILE=<checksummed-restore-result.json>
export LIQI_RESTORED_SOURCE_REVISION=<same-40-character-git-sha>
export LIQI_RECOVERY_STATUS_OUTPUT=<recovery-status-v1.json>
PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=liqi PGUSER=liqi_backup \
  bash database/bin/recovery-status-v1.sh
```

Expected result: exit zero and `status=passed`. Missing, stale, schema-invalid, checksum-invalid, over-RTO, or SHA-mismatched evidence exits non-zero.
