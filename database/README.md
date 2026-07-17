# PostgreSQL Authority V0

This directory owns the LIQI V0 PostgreSQL authority, least-privilege roles, forward-only migration lifecycle, PgBouncer policy, transactional outbox foundation and database-local validation tools.

It intentionally contains no LIQI product/domain schema.

## Provider contract

- Schema: `contracts/platform/database-v0.schema.json`
- Accepted V0 example: `contracts/platform/database-v0.example.json`
- Decision records: `docs/adr/0200-*` and `docs/adr/0201-*`

Validate without starting PostgreSQL:

```bash
database/tests/run-source-validation.sh
```

## Runtime boundary for Senior 3

Runtime processes connect only through PgBouncer transaction pooling. They must not depend on session state, temporary tables, session advisory locks, `LISTEN/NOTIFY` through the pool, or named prepared statements unless the selected driver proves compatibility.

Approved persistence functions after migration version 2:

- Producer: `platform.enqueue_outbox_v0(...)`
- Walking skeleton producer: `platform.request_probe_v0(...)`
- Worker claim: `platform.claim_outbox_v0(...)`
- Worker acknowledgement: `platform.ack_outbox_v0(...)`
- Worker retry/dead letter: `platform.fail_outbox_v0(...)`
- Probe idempotent effect: `platform.apply_probe_effect_and_ack_v0(...)`
- Readiness: `platform.database_readiness_v0(required_version)`

The wire adapter must preserve event ID, type, version, occurred-at, aggregate key, ordering key and payload. `database/tests/contract/validate_wire_mapping.py` consumes Senior 3's accepted example; the placeholder fixture is not a wire contract and states its removal condition.

## Cluster lifecycle

Production bootstrap, through a local administrative Unix socket:

```bash
database/bin/bootstrap-cluster.sh
```

Forward-only migration, using standard libpq environment variables and a secret-backed `PGPASSFILE` when password authentication is required:

```bash
PGHOST=/run/postgresql \
PGDATABASE=liqi \
PGUSER=liqi_migrator \
PGPASSFILE=/run/liqi/secrets/database/migrator-pgpass \
database/bin/migrate.sh
```

No script accepts a password or plaintext DSN argument. Runtime credentials are materialized outside Git by the Senior 1 host contract.

## Integration validation

See `database/tests/README.md`. The local Windows workspace used to author V0 does not contain PostgreSQL binaries, so integration tests must run on a disposable PostgreSQL 17 host before merge/release.
