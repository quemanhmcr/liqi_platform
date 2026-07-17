# Database V0 validation

## Source-only gate

Does not start PostgreSQL and does not build an image:

```bash
database/tests/run-source-validation.sh
```

Python validation dependencies are pinned in `database/requirements-validation.txt`.

## PostgreSQL integration gate

Requires a disposable PostgreSQL 17 cluster with `pgtap`, `pg_prove`, `psql`, `sha256sum` and a local administrative connection. It creates only a database named `liqi_v0_test` by default:

```bash
LIQI_TEST_DATABASE=liqi_v0_test \
  database/tests/integration/run_database_tests.sh
```

The gate proves fresh migration, rerun idempotency, advisory migration locking, role/grant boundaries, timeout policy, atomic probe/outbox insertion, concurrent claim exclusion, lease reclaim, idempotent acknowledgement, bounded retry and dead-letter transition.

The wire mapping test is activated after Senior 3 publishes the accepted wire example:

```bash
python database/tests/contract/validate_wire_mapping.py \
  contracts/events/<senior-3-wire-example>.json
```
