# liqi_persistence

Provider-owned Ecto boundary for V1. It publishes three bounded Repo modules plus function-only command, outbox, handoff, readiness, observation, maintenance, and runtime-adapter APIs.

## Runtime integration

`LiqiPersistence.RuntimeAdapter` implements the callback shape published by Senior 1:

- `readiness/1`
- `request_probe/1`
- `observe_probe/2`
- `claim_probe_events/2`
- `apply_probe_effect/3`
- `fail_event/5`
- `read_handoff/2`

The adapter calls the consumer command module's `event_id/1` function dynamically. It does not copy command identity semantics into the database provider.

The dependency app starts with `start_repos: false`. Senior 1, as supervision/runtime-config owner, must either:

1. configure `:liqi_persistence, :repos` to the already-supervised `Liqi.Persistence.ApiRepo`, `Liqi.Persistence.RealtimeRepo`, and `Liqi.Persistence.WorkerRepo`; or
2. explicitly set `start_repos: true` and supervise only the provider Repo set.

Both paths use one logical pool set of 12 command, 4 realtime, and 6 worker clients. Starting both Repo sets is outside contract.

Example consumer configuration:

```elixir
config :liqi_persistence,
  start_repos: false,
  repos: %{
    command: Liqi.Persistence.ApiRepo,
    realtime: Liqi.Persistence.RealtimeRepo,
    worker: Liqi.Persistence.WorkerRepo
  }

config :liqi_platform,
  persistence_adapter: LiqiPersistence.RuntimeAdapter
```

The root runtime should replace its duplicate inline Oban options with exactly one child using the provider policy:

```elixir
{Oban, LiqiJobs.Config.oban_options()}
```

Senior 1 remains owner of root Mix dependencies, lockfile, secret resolution, release configuration, and supervision topology.

## Independent provider validation

```bash
bash database/tests/run-source-validation.sh
LIQI_DATABASE_INTEGRATION=1 bash database/tests/integration/run_beam_provider_tests.sh
```

The integration runner uses disposable local password files only to satisfy the provider's fail-closed credential interface. Production credentials are materialized by the runtime/deployment owner and must never be committed.
