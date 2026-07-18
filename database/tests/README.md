# Database V1 validation

## Source-only gate

```bash
bash database/tests/run-source-validation.sh
```

This gate validates all V0/V1 contracts and examples, immutable V0 migration checksums, migration 5–8 source, PostgreSQL parsing, connection/capacity accounting, PgBouncer transaction-mode policy, BEAM provider boundaries, recovery composition, secret hygiene, shell syntax, and executable modes. It performs no OCI mutation.

## PostgreSQL 17 integration gate

Use a disposable PostgreSQL 17 cluster with pgTAP 1.3.4 or newer, `psql`, `sha256sum`, and an administrative local connection:

```bash
LIQI_TEST_DATABASE=liqi_v1_test \
LIQI_V0_UPGRADE_DATABASE=liqi_v0_upgrade_test \
  bash database/tests/integration/run_database_tests.sh
```

The runner proves:

- fresh migration 1 through 8 and safe rerun
- advisory migration locking
- V0 migration-4 to V1 migration-8 upgrade compatibility
- least privilege and no runtime superuser/table-authority access
- idempotency concurrency and stable duplicate outcome
- optimistic aggregate-version conflicts
- atomic outbox insertion and no pre-commit handoff visibility
- concurrent claim exclusion, lease reclaim, idempotent ack, retry, and dead-letter bounds
- V0 and V1 committed handoff/resume/gap behavior
- Oban migration 14 storage, logged jobs, unlogged peer coordination, and bounded retention
- backup verification invariants

The local acceptance run for this branch used PostgreSQL 17.10 and passed 174 pgTAP assertions plus all shell concurrency/upgrade gates.

## BEAM provider integration gate

Run against the same disposable PostgreSQL 17 database after migration 8:

```bash
PGHOST=127.0.0.1 \
PGPORT=5432 \
PGDATABASE=liqi_v1_test \
MIX=mix \
  bash database/tests/integration/run_beam_provider_tests.sh
```

The runner materializes disposable test-only credential files and redirects Mix build, dependency, lock, Hex, and Rebar state to a temporary directory. It compiles both provider apps with warnings-as-errors, runs the Ecto/runtime-adapter callback tests, and verifies Oban insert/cancel behavior without leaving `_build`, `deps`, or app-local `mix.lock` artifacts in the repository.

Set `LIQI_RUN_BEAM_INTEGRATION=1` when invoking `run_database_tests.sh` to include this gate in the full disposable-database run.

Senior 1 remains owner of the root `mix.lock`, release supervision, and production Elixir/OTP pin.

## PgBouncer and OCI evidence

Static validation confirms `pool_mode=transaction`, bounded pools, unnamed Postgrex preparation, and no session-state design. Production readiness still requires an actual provider run through deployed PgBouncer and evidence of server-pool limits. A direct PostgreSQL test is not a substitute.

Live migration, backup repository writes, restore drills, and traffic changes require the approvals described in `database/runbooks/v1-durable-plane-activation.md`.
