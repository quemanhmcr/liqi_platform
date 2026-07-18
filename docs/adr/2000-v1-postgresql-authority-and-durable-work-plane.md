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
4. V1 command idempotency is durable in `platform.command_idempotency_v1`. A transaction-scoped advisory lock serializes a `(scope, key)` pair. The consumer command module owns a stable lowercase SHA-256 request fingerprint; PostgreSQL treats it as an opaque 64-character digest and never defines a competing JSON canonicalization. The same digest returns the original outcome; reuse of the key with a different digest fails with SQLSTATE `LQ001`.
5. Aggregate commands compare an expected version under a row lock and increment by one. A stale version fails with SQLSTATE `LQ002`. The V1 walking skeleton only creates the platform probe from version 0 to version 1; product updates require their own versioned commands.
6. The V0 outbox table remains the single domain-event authority. V1 adds shared-envelope columns and versioned functions over the same rows. Delivery is at-least-once; consumers apply an idempotent terminal effect before acknowledgement.
7. V1 realtime uses a separate versioned handoff projection and cursor. This prevents the Rust V0 realtime reader from receiving protocol-v1 events it cannot decode. The projection is not an authority. Its retention watermark detects a cursor gap and fails with `LQ004`, requiring authority resynchronization.
8. Ecto SQL 3.14.x, Postgrex 0.22.3+, and Decimal 3.x are the supported dependency line. Oban 2.23.x is pinned to PostgreSQL migration 14 in schema `oban`. Migration 7 is flattened from the official Oban v2.23.0 PostgreSQL migrations at source commit `9f5707256e62dc84ef72e000bf9a051d111a0efc` and is checked through `pg_catalog` parity assertions. `oban_jobs` is logged and part of backup/PITR. `oban_peers` is unlogged because leadership is rebuildable runtime coordination state.
9. Oban does not replace the outbox. It is limited to scheduled maintenance, provider calls, push, media orchestration, cleanup, delayed retry, and explicitly activated recovery work.
10. PgBouncer transaction pooling cannot carry PostgreSQL `LISTEN/NOTIFY`. Oban therefore uses `Oban.Notifiers.PG`, which communicates through BEAM process groups rather than the database. V1 is a single BEAM node, so notifier status is expected to be solitary rather than clustered; the bounded one-second stager/polling path remains the recovery mechanism. Background wake-up latency is acceptable; realtime never depends on Oban.
11. Oban uniqueness is admission deduplication only. Terminal effects still require database idempotency because open-source Oban uniqueness is not an exactly-once guarantee.
12. Terminal outbox, handoff, idempotency, and Oban rows have bounded batch-prune policies. Rows referenced by recovery/probe invariants are retained until the referencing projection is removed through a future contract migration.
13. `LiqiPersistence.RuntimeAdapter` implements the callback originally reviewed at `v1/beam-runtime-realtime@96ce092e0381225e840e70ce5c81e076f6b8499a`, including the canonical `observe_probe/2` seam. Senior 1 source-integrated the provider at merge `d2616943a8a140bb867a81face1f72ea4503d4f7`. Senior 1 remains owner of event identity through the command module's `event_id/1`, Repo supervision, and runtime admission. The provider converts the runtime envelope's Unix epoch millisecond deadline to PostgreSQL `timestamptz`; expired commands are rejected before a database transaction is opened.
14. Merge `d2616943a8a140bb867a81face1f72ea4503d4f7` imports the provider source but intentionally does not complete root wiring: `beam/config/config.exs` still selects `Liqi.Persistence.PostgresV1`, and Repo/Oban start flags remain disabled. Removal condition: configure `LiqiPersistence.Repos` to the three Senior 1 Repo modules, supervise exactly one Oban instance, pass provider/consumer integration tests, replace `:persistence_adapter` with `LiqiPersistence.RuntimeAdapter`, and then remove the placeholder. Senior 1 owns root wiring and placeholder removal; Senior 2 owns callable database compatibility.
15. Provider dependency applications do not start Repo or Oban children by default. Senior 1 configures the single supervised Repo set through `:liqi_persistence, :repos` and owns the root Oban child. Starting both provider-default and runtime-owned children would double the declared 22-client demand and violate connection accounting.
16. V1 callable outputs retain canonical shared-envelope fields and add read-only consumer aliases `actor_key`→`aggregate_key`, `payload_type`→`event_type`, and `payload_version`→`event_version`. No duplicate durable columns or semantics are created. `observe_probe_v1` returns no row for an unknown probe and SQLSTATE `LQ003` when the probe exists but the requested event identity differs.

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

Local provider evidence completed:

- PostgreSQL 17.10 fresh migration and migration-4-to-8 upgrade tests.
- Official pgTAP 1.3.4 at commit `968eb53a33114e83042b3bdb0c664b5b80cf8bdf` loaded source-locally against a dedicated fresh database: 174/174 assertions across seven files.
- Migration rerun and advisory-lock serialization, concurrent claim, concurrent idempotency, V0/V1 commit visibility, migration-4-to-8 upgrade, gap repair, and Oban migration-14 catalog parity.
- Elixir 1.20.2 / OTP 29 compile with warnings-as-errors, direct Ecto/runtime-adapter integration (including Senior 1's observation shape), Oban insert/cancel integration, and Hex advisory audit. Remaining compile warnings originate in the selected upstream Postgrex/Oban sources under Elixir 1.20 and do not originate in LIQI provider files.
- Exact-SHA provider revalidation at `407ed3aac33f468f29224f826922e03e0abd79aa` used PostgreSQL 17.10 and official pgTAP 1.3.4 commit `968eb53a33114e83042b3bdb0c664b5b80cf8bdf`: 174/174 assertions, all concurrency/visibility/upgrade gates, provider compile/tests, and both Hex audits passed.
- A detached consumer check at Senior 1 merge `d2616943a8a140bb867a81face1f72ea4503d4f7` added only the documented path dependencies, Repo mapping, adapter switch, and `{Oban, LiqiJobs.Config.oban_options()}`. Root compile completed and 41/41 tests plus Hex audit passed after normalizing a pre-existing release-overlay line-ending mismatch in the disposable worktree. This is compatibility evidence, not a substitute for the Senior 1-owned wiring commit.

Environment evidence still required:

- The approved V0 readiness tag and exact-SHA owner evidence.
- An actual runtime path through deployed PgBouncer transaction pooling and its server-pool metrics.
- Live OCI migration, backup/WAL freshness, isolated restore, and exact-release recovery evidence.

## Primary sources

- PostgreSQL 17 documentation for transaction timeouts and `FOR UPDATE SKIP LOCKED`.
- Ecto SQL documentation for PostgreSQL `prepare: :unnamed` and migration locking.
- Oban 2.23.0 source and Hex documentation for migration 14, queue concurrency, worker timeouts, pruning, lifeline recovery, uniqueness, and PgBouncer notifier behavior.
