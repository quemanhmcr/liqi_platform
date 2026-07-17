# Database V0 validation

## Source-only gate

This gate starts no PostgreSQL process, builds no image and performs no OCI mutation:

```bash
database/tests/run-source-validation.sh
```

It validates JSON Schemas/examples, migration SHA-256 history, PostgreSQL parser acceptance, PgBouncer/pgBackRest policy, secret-boundary behavior, restore safety guards, systemd resource bounds, recovery evidence behavior and shell syntax. Python dependencies are pinned in `database/requirements-validation.txt`.

## PostgreSQL integration gate

Requires a disposable PostgreSQL 17 cluster with `pgtap`, `pg_prove`, `psql`, `sha256sum` and a local administrative connection. It creates only a database named `liqi_v0_test` by default:

```bash
LIQI_TEST_DATABASE=liqi_v0_test \
  database/tests/integration/run_database_tests.sh
```

The gate proves fresh migration, rerun idempotency, advisory migration locking, role/grant boundaries, timeout policy, atomic probe/outbox/handoff insertion, invisibility before producer commit, visibility after commit, concurrent claim exclusion, lease reclaim, idempotent acknowledgement, bounded retry, dead-letter transition and recovery-probe invariants.

## pgBackRest/OCI recovery gate

After Senior 1 supplies approved host paths, bucket output and systemd credentials, and after the project owner explicitly permits repository mutation:

```bash
database/bin/render-pgbackrest-config.sh
database/bin/pgbackrest-command.sh --stanza=liqi stanza-create
database/bin/pgbackrest-command.sh --stanza=liqi check
database/bin/backup.sh full
database/recovery/restore-exercise.sh
```

Expected evidence is a valid `database-backup-metadata-v0` document plus SHA-256 sidecar and a `database-restore-result-v0` document with `success=true`. Source validation cannot substitute for this drill.

## Wire mapping gate

Senior 3 publishes the accepted V0 example at `contracts/events/examples/platform-probe-requested-v0.json`. After branch integration, run:

```bash
python database/tests/contract/validate_wire_mapping.py \
  contracts/events/examples/platform-probe-requested-v0.json
```
