# ADR 1004: V1 persistence provider integration without duplicate runtime resources

- Status: Accepted for source integration; disposable PostgreSQL evidence pending
- Date: 2026-07-18
- Decision owner: Senior 1
- Provider: Senior 2
- Consumers: Senior 4, Senior 5

## Context

The initial Senior 1 adapter was intentionally fail-closed because the first Senior 2 checkpoint had semantic contracts but no callable migration-8 API. Senior 2 later published migrations 5–8 and callback-complete applications in `beam/apps/liqi_persistence` and `beam/apps/liqi_jobs`.

The provider contribution crossed the nominal `beam/**` path boundary. Copying its SQL mapping into the root application would duplicate provider semantics. Converting the project to an umbrella would change the release topology, while starting provider-owned Repo or Oban children would duplicate resource lifecycle.

## Decision

1. Keep `liqi_platform` as one root Mix project and one OTP release. The provider Mix projects are internal path dependencies, not umbrella children or independently deployed services.
2. Use `LiqiPersistence.RuntimeAdapter` directly as the V1 production persistence adapter and remove the temporary rejecting `Liqi.Persistence.PostgresV1` adapter.
3. Map the provider to the existing root Repo modules: command → `Liqi.Persistence.ApiRepo`, realtime → `Liqi.Persistence.RealtimeRepo`, worker → `Liqi.Persistence.WorkerRepo`.
4. Keep `:liqi_persistence, start_repos: false` and `:liqi_jobs, start_oban: false`. Senior 1's root supervisor remains the only lifecycle owner.
5. Supervise exactly one Oban instance from `LiqiJobs.Config.oban_options/0`. Active concurrency is six, total configured slots are seven, and recovery starts paused. Oban never replaces the domain outbox.
6. Graceful drain rejects new commands first, then pauses all local `LiqiJobs.Oban` queues. Pause failure is observable and the drain command fails closed rather than claiming success.
7. Retain `Liqi.Persistence.Postgres` only as the explicit V0 rollback adapter. It is non-default and never dual-writes.
8. Publish one `database.secretRef`. For schema V1 it resolves to a bounded `role-url-bundle-v1` JSON object containing exactly `command`, `realtime`, and `worker` PostgreSQL URLs.
9. Validate each URL against its database username (`liqi_api`, `liqi_realtime`, `liqi_worker`). Production additionally requires PgBouncer at `127.0.0.1:6432/liqi`, with no query or fragment.
10. Retain `LIQI_API_DATABASE_SECRET_REF`, `LIQI_REALTIME_DATABASE_SECRET_REF`, and `LIQI_WORKER_DATABASE_SECRET_REF` for one release window only. They remain compatibility references, never the V1 default.
11. Map SQLSTATE `LQ003` to stable error `probe.identity_mismatch` and `LQ004` to realtime gap repair.
12. After merge, Senior 1 retains path-level single-writer control of `beam/apps/**`; Senior 2 remains DRI for durable semantics and supplies exact provider commits for future updates. Direct concurrent edits to the integrated provider slice are forbidden.

## Ownership after integration

- Senior 2 owns SQL functions, durable semantics, and provider query/result mapping.
- Senior 1 owns root dependency selection, Repo implementations, runtime config, supervision, admission, drain, and public error mapping.
- Provider applications must remain start-empty by default. Starting a Repo, Oban, or durable worker inside those dependencies is a breaking lifecycle change.

## Compatibility and rollback

The change is additive for protocol V1 and removes only a temporary unavailable adapter. V0 functions remain in PostgreSQL and the Rust V0 runtime remains the route-scoped rollback target. There is one durable authority and no dual write. Application rollback does not run down migrations.

## Validation

```bash
MIX_ENV=test mix compile --warnings-as-errors
MIX_ENV=test mix test --seed 0
bash beam/scripts/validate-v1-source.sh --output .artifacts/runtime-source.json
LIQI_TEST_DATABASE_URL=<disposable-postgresql-17> \
  bash beam/scripts/run-v1-integration.sh --output .artifacts/runtime-integration.json
```

Disposable PostgreSQL evidence remains mandatory. Source tests and fixtures do not substitute for migration-8 execution evidence.

## OCI impact

None. Live migration, deployment, secret mutation, and traffic changes remain approval-gated and Senior 4-owned.
