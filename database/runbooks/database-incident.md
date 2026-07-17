# PostgreSQL V0 incident runbook

## First principles

- PostgreSQL remains the only durable authority.
- PgBouncer/realtime/worker failure must not invent alternate state.
- Security failure is fail closed.
- Single-node V0 has no automatic failover; do not describe restart or restore as HA.

## Connection saturation

1. Read `liqi_database_connection_saturation_ratio` and PgBouncer waiting clients.
2. Stop unbounded clients/retries before increasing pools.
3. Preserve 30 PostgreSQL connections of direct/reserved headroom.
4. A pooling-mode or connection-budget change is a database contract change for Senior 3.

## Long or idle transactions

- Runtime roles have bounded statement, lock and idle-in-transaction timeouts.
- Identify actor/application name through `pg_stat_activity`; do not log query parameters or credentials.
- Cancel the offending backend before terminating it when safe.

## Disk pressure

- Protect PostgreSQL data and WAL first.
- Do not delete WAL required by an active backup or restore.
- Do not delete the only backup to recover free-tier space.
- Stop optional logs/images, expire according to policy, or approve PAYG capacity.

## Migration failure

- Read machine-readable migration status and `platform.migration_runs`.
- Never edit an applied migration or run destructive rollback SQL.
- Repair with a new additive/forward-only migration.

## Backup/archive failure

Use `backup-status.sh`. If WAL archive is stale or failed after the last success, recovery readiness is false even when application traffic is healthy. Follow `backup-and-wal.md` and complete a new full backup plus restore drill before restoring the RPO claim.

## Host loss

1. Provision a fresh host from Senior 1 IaC/bootstrap; do not hand-repair an unknown host.
2. Restore the newest verified backup into an isolated directory first.
3. Review machine-readable invariants and recovery target.
4. Only after owner approval, adapt the validated restored directory to the production service path.
5. Rotate credentials if host compromise is possible.
