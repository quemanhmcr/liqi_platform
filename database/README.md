# PostgreSQL Authority V0

This directory owns the LIQI V0 PostgreSQL authority, least-privilege roles, forward-only migration lifecycle, PgBouncer policy, transactional outbox foundation and database-local validation tools.

It intentionally contains no LIQI product/domain schema.

## Provider contract

- Schema: `contracts/platform/database-v0.schema.json`
- Example/accepted V0 values: `contracts/platform/database-v0.example.json`
- Validate without starting PostgreSQL:

```bash
python database/tools/validate_database_contract.py
```

Runtime consumers use PgBouncer transaction pooling. Migrations, recovery and administrative probes use the restricted direct PostgreSQL endpoint. Secrets are opaque OCI-host references and must be materialized outside Git.

## Current checkpoints

1. Database contract: connection, roles, migration/readiness, outbox, recovery and metrics semantics.
2. Authority walking skeleton: SQL migrations, grants, PgBouncer policy and outbox tests.
3. Recovery proven: pgBackRest policy, machine-readable metadata, restore and invariant verification.
