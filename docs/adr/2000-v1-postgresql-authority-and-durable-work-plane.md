# ADR 2000: V1 PostgreSQL authority and durable work plane

- Status: Accepted for source implementation; environment evidence pending
- Date: 2026-07-18
- Decision owner: Senior 2
- Consumers: Senior 1, Senior 4, Senior 5

## Context

V0 established PostgreSQL 17, forward-only SQL migrations, a durable outbox, a committed platform-probe handoff, PgBouncer transaction pooling, and pgBackRest recovery seams. V1 introduces an Elixir/Ecto runtime and Oban without changing durable authority or forcing Rust V0 to understand V1 wire events during the rollback window.

The V0 closeout requires an approved `v0-platform-foundation-ready` checkpoint and exact-SHA owner evidence before V1 readiness. The repository currently has neither the approved tag nor complete environment evidence. Source work therefore starts from `main@4c561515f46237acfaf64e0145e37e54a6c4c9d9` and remains `engineering-complete-evidence-pending` until that prerequisite is satisfied.

## Decision

1. PostgreSQL remains the only durable authority. Ecto orchestrates transactions and maps function results; runtime consumers do not receive authority-table grants.
2. Existing V0 migrations remain immutable. V1 is additive and raises the required migration from 4 to 8.
3. Runtime Ecto connections use PgBouncer transaction mode, unnamed prepared statements, no session state, no `LISTEN`, and bounded pools. Migrations continue through the direct PostgreSQL endpoint and the existing `platform.schema_migrations` registry; Ecto startup migration is disabled.
4. V1 command idempotency is durable in `platform.command_idempotency_v1`. A transaction-scoped advisory lock serializes a `(scope, key)` pair. The same logical request returns the original outcome; reuse for a different request fails with SQLSTATE `LQ001`.
5. Aggregate commands compare an expected version under a row lock and increment by one. A stale version fails with SQLSTATE `LQ002`. The V1 walking skeleton only creates the platform probe from version 0 to version 1; product updates require their own versioned commands.
6. The V0 outbox table remains the single domain-event authority. V1 adds shared-envelope columns and versioned functions over the same rows. Delivery is at-least-once; consumers apply an idempotent terminal effect before acknowledgement.
7. V1 realtime uses a separate versioned handoff projection and cursor. This prevents the Rust V0 realtime reader from receiving protocol-v1 events it cannot decode. The projection is not an authority. Its retention watermark detects a cursor gap and fails with `LQ004`, requiring authority resynchronization.
8. Oban 2.23.x is pinned to PostgreSQL migration 14 in schema `oban`. `oban_jobs` is logged and part of backup/PITR. `oban_peers` is unlogged because leadership is rebuildable runtime coordination state.
9. Oban does not replace the outbox. It is limited to scheduled maintenance, provider calls, push, media orchestration, cleanup, delayed retry, and explicitly activated recovery work.
10. PgBouncer transaction pooling cannot carry PostgreSQL `LISTEN/NOTIFY`. Oban therefore uses `Oban.Notifiers.PG` and the bounded one-second local stager fallback on the single BEAM node. Background wake-up latency is acceptable; realtime never depends on Oban.
11. Oban uniqueness is admission deduplication only. Terminal effects still require database idempotency because open-source Oban uniqueness is not an exactly-once guarantee.
12. Terminal outbox, handoff, idempotency, and Oban rows have bounded batch-prune policies. Rows referenced by recovery/probe invariants are retained until the referencing projection is removed through a future contract migration.

## Capacity and connection accounting

- PostgreSQL `max_connections`: 80.
- PgBouncer server capacity: 40.
- Direct migration/monitor/backup reservation: 10.
- Reserved PostgreSQL headroom: 30.
- Ecto client pools: command 12, realtime 4, jobs 6. These are logical clients and are not added again as PostgreSQL backends.
- Oban configured concurrency: 6 active plus one paused recovery slot.
- Database provider hard ceilings remain 1.2 OCPU, 7,936 MiB memory, and 130.2 GiB disk within the fixed V1 host envelope.

## Compatibility and migration

- Rust V0 may continue reading V0 functions and tables during the route-scoped rollback window.
- Only one runtime may command-write a route/capability. There is no dual write.
- Application rollback never runs database down migrations.
- V1 handoff and idempotency tables may be removed only after the Rust V0 rollback window closes, all V1 consumers have advanced, retention watermarks are drained, and a contract-stage migration is separately approved.
- The Oban table comment records migration version 14 because Oban uses that comment for migration detection after restore.

## Validation and evidence

Source validation:

```bash
python database/tests/contract/validate_v1_contracts.py
bash database/tests/run-source-validation.sh
```

Required but currently unavailable in this worktree:

- PostgreSQL/pgTAP integration against a fresh database and V0 upgrade database.
- Elixir compile/test because Erlang/Elixir are not installed locally and root Mix ownership belongs to Senior 1.
- Live OCI migration, backup, WAL freshness, and isolated restore verification.

## Primary sources

- PostgreSQL 17 documentation for transaction timeouts and `FOR UPDATE SKIP LOCKED`.
- Ecto SQL documentation for PostgreSQL `prepare: :unnamed` and migration locking.
- Oban 2.23.0 source and Hex documentation for migration 14, queue concurrency, worker timeouts, pruning, lifeline recovery, uniqueness, and PgBouncer notifier behavior.
