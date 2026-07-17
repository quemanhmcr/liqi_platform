# PostgreSQL V0 provision and migration runbook

## Preconditions

- PostgreSQL 17.10 or newer PostgreSQL 17 security minor, PgBouncer 1.24 or newer and pgBackRest 2.58.0 or newer are installed from reviewed binary repositories.
- Senior 1 has materialized the host directories and private/loopback-only network boundary.
- No PostgreSQL or PgBouncer listener is reachable from the Internet.
- Role passwords and backup credentials exist only in OCI Vault/systemd credentials or root-only tmpfs paths.

## Source validation

```bash
database/tests/run-source-validation.sh
```

Expected: three machine-readable success records for database contract, source and shell syntax, plus recovery contract success. This command does not start PostgreSQL or mutate OCI.

## Cluster bootstrap

Run as the local PostgreSQL administrative operating-system identity:

```bash
database/bin/bootstrap-cluster.sh
```

Expected: `cluster bootstrap complete`; role passwords remain unset by source and must be rotated/materialized separately.

## Apply migrations

```bash
PGHOST=/run/postgresql \
PGDATABASE=liqi \
PGUSER=liqi_migrator \
PGPASSFILE=/run/liqi/secrets/database/migrator-pgpass \
  database/bin/migrate.sh
```

Expected JSON has `ready=true`, `currentVersion=4` and `reason=ready`. A second invocation must report all migrations already applied without changing rows.

## Readiness

Use PgBouncer, not the direct PostgreSQL endpoint:

```bash
LIQI_REQUIRED_MIGRATION_VERSION=4 \
PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=liqi PGUSER=liqi_monitor \
PGPASSFILE=/run/liqi/secrets/database/monitor-pgpass \
  database/bin/readiness.sh
```

Pending or failed migrations return non-zero and must fail closed. Liveness is a separate direct `SELECT 1`-class probe and does not imply schema readiness.

## Failure diagnosis

- Check `platform.migration_runs` for the failed version and stable error code.
- Do not edit a migration already merged to `main`; create a new forward-only migration.
- If a checksum differs, stop. Determine whether source or database history is unauthorized before proceeding.
- Do not grant runtime roles DDL or use PostgreSQL superuser for API/realtime/worker traffic.
