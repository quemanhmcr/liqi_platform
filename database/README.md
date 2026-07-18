# PostgreSQL Authority and Durable Work Plane

This directory owns the LIQI PostgreSQL durable authority from V0 through the additive V1 migration. V1 keeps every V0 migration immutable and adds Ecto-facing functions, durable command idempotency, aggregate versions, a protocol-v1 outbox envelope, committed realtime handoff, Oban migration 14 storage, bounded retention, readiness, metrics, and recovery evidence.

It intentionally contains no product-domain schema and creates no second durable event authority.

## Published V1 contracts

- Runtime and connection semantics: `contracts/database/database-runtime-v1.schema.json`
- Required migration/readiness: `contracts/database/migration-readiness-v1.schema.json`
- Shared durable outbox: `contracts/database/outbox-v1.schema.json`
- Committed realtime handoff: `contracts/database/realtime-handoff-v1.schema.json`
- Command idempotency/versioning: `contracts/database/idempotency-v1.schema.json`
- Oban durable-work policy: `contracts/jobs/oban-policy-v1.schema.json`
- Recovery status: `contracts/database/recovery-status-v1.schema.json`
- Decision: `docs/adr/2000-v1-postgresql-authority-and-durable-work-plane.md`

Run the source-only provider gate without starting PostgreSQL or mutating OCI:

```bash
bash database/tests/run-source-validation.sh
```

## Runtime boundary for Senior 1

Runtime processes connect through PgBouncer transaction pooling. The provider requires unnamed prepared statements and forbids reliance on temporary tables, session state, session advisory locks, or `LISTEN/NOTIFY` through the runtime pool. Migration, backup, and administrative verification continue through the restricted direct PostgreSQL endpoint.

Required migration versions:

- Platform schema migration: `8`
- Oban migration: `14`

Approved V1 functions:

- Command transaction: `platform.request_probe_v1(uuid, uuid, text, text, text, bigint, timestamptz, uuid, uuid, jsonb, timestamptz, jsonb)`; argument five is the consumer-owned lowercase SHA-256 request fingerprint.
- Command idempotency lookup: `platform.read_idempotency_v1(scope, key)`
- Worker claim: `platform.claim_outbox_v1(consumer_id, batch_size, lease_seconds)`
- Worker acknowledgement: `platform.ack_outbox_v1(...)`
- Worker retry/dead-letter: `platform.fail_outbox_v1(...)`
- Idempotent terminal effect: `platform.apply_probe_effect_and_ack_v1(...)`
- Realtime reader: `platform.read_realtime_handoff_v1(after_handoff_id, batch_size)`
- Walking-skeleton observation: `platform.observe_probe_v1(probe_id, event_id)`
- Runtime readiness: `platform.database_readiness_v1(8, 14)`

Senior 1 consumes these through `LiqiPersistence.RuntimeAdapter`; runtime code does not receive table-level authority grants. The adapter callback surface is `readiness/1`, `request_probe/1`, `observe_probe/2`, `claim_probe_events/2`, `apply_probe_effect/3`, `fail_event/5`, and `read_handoff/2`. Configure `:liqi_persistence, :repos` to the existing Senior 1 command/realtime/worker Repo modules and keep provider auto-start disabled so only one pool set exists.

## Ecto and Oban provider apps

`beam/apps/liqi_persistence` publishes three bounded repositories:

- command pool: 12 clients, 5-second query budget
- realtime pool: 4 clients, 3-second query budget
- worker/Oban pool: 6 clients, 30-second query budget

All use `prepare: :unnamed`. Credentials are read from secret files and are never accepted as source-controlled DSNs.

`beam/apps/liqi_jobs` publishes the Oban policy and bounded worker modules. Configured concurrency is seven slots: six active and one recovery slot paused by default. Oban stores durable work in logged `oban.oban_jobs`; it does not replace `platform.outbox_events` and it makes no exactly-once claim.

## Migration lifecycle

Forward-only migration uses the direct endpoint and standard libpq environment variables:

```bash
PGHOST=/run/postgresql \
PGPORT=5432 \
PGDATABASE=liqi \
PGUSER=liqi_migrator \
PGPASSFILE=/run/liqi/secrets/database/migrator-pgpass \
  bash database/bin/migrate.sh
```

Application rollback does not run a database down migration. Rust V0 functions remain present during the route-scoped rollback window, and migration 5 repairs their PostgreSQL 17 name resolution without changing their signatures.

## Provider commands for operations

- Direct liveness: `database/bin/liveness.sh`
- V0 compatibility readiness: `database/bin/readiness.sh`
- V1 migration/write readiness: `database/bin/readiness-v1.sh`
- PostgreSQL/outbox/WAL metrics: `database/bin/postgres-metrics.sh`
- V1 outbox/handoff/Oban metrics: `database/bin/durable-work-metrics-v1.sh`
- PgBouncer metrics: `database/bin/pgbouncer-metrics.sh`
- Backup status: `database/bin/backup-status.sh`
- V1 exact-SHA recovery status: `database/bin/recovery-status-v1.sh`
- Recovery execution and verification: `database/bin/run-restore-drill-v1.sh` (readiness provider) and `database/recovery/*.sh` (provider lifecycle primitives)

Backup freshness does not prove restore. `recovery-status-v1.sh` remains non-zero until checksummed, schema-valid backup and restore evidence refer to the same exact source revision and meet the 300-second RPO and 3,600-second RTO working targets.

## Evidence boundary

Local PostgreSQL 17 integration can prove migration and durability semantics. It cannot prove the deployed PgBouncer path, independent pgBackRest repository/WAL freshness, isolated restore, live capacity, or the approved V0 checkpoint. Those remain environment evidence and must not be replaced by fixtures.

See `database/tests/README.md` and `database/runbooks/v1-durable-plane-activation.md`.
