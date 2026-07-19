# V1 self-hosted OpenTofu PostgreSQL backend

This directory belongs to the independent management/storage plane. Never run it on the OCI application host or against the application PostgreSQL cluster.

## Fixed authority boundary

- database: `liqi_infra_state`
- OpenTofu schema: `opentofu_v1_live`
- runtime role: `liqi_tofu_state`
- backend: OpenTofu `pg`
- lock mechanism: PostgreSQL advisory locks
- transport: libpq TLS with `sslmode=verify-full`
- state and saved plans: OpenTofu encryption enforced through protected `TF_ENCRYPTION`

OpenTofu stores its state ID sequence in `public`, even when a custom state schema is selected. Bootstrap therefore grants narrowly bounded temporary create privileges. After the first successful `tofu init`, run `finalize-privileges.sh` to revoke create privileges while retaining access to the existing state table, index, and sequence.

## Protected inputs

Use a root/operator-owned libpq service file with mode `0600` or `0400`. Do not pass passwords or a DSN on command lines and do not commit them.

```text
PGSERVICEFILE=/protected/path/pg_service.conf
STATE_ADMIN_SERVICE=liqi-state-admin
STATE_RUNTIME_SERVICE=liqi-state-runtime
STATE_ROLE_PASSWORD_FILE=/protected/path/state-role-password
STATE_RUNTIME_PASSFILE=/protected/path/runtime.pgpass
STATE_RUNTIME_CREDENTIAL_FILE=/protected/path/state-role-password
STATE_ENCRYPTION_PASSPHRASE_FILE=/protected/path/state-encryption-passphrase
PGPASSFILE=/protected/path/runtime.pgpass
STATE_BACKUP_PASSPHRASE_FILE=/protected/path/state-backup-passphrase
STATE_BACKUP_DIR=/independent-storage/opentofu-state
TF_ENCRYPTION=<protected OpenTofu encryption configuration>
LIQI_SOURCE_GIT_SHA=<exact clean source SHA>
```

The runtime OpenTofu shell must export `PG_CONN_STR` with `sslmode=verify-full`, `PG_SCHEMA_NAME=opentofu_v1_live`, and all three `PG_SKIP_*_CREATION=true` flags after bootstrap finalization.

## Required sequence

```bash
./scripts/bootstrap.sh
# First init only, with temporary schema/table/index creation privileges.
./scripts/with-protected-environment.sh tofu -chdir=../../opentofu/environments/v1-live init -reconfigure -input=false
./scripts/finalize-privileges.sh
./scripts/test-locking.sh --output /protected/evidence/locking.json
./scripts/backup.sh --output /protected/evidence/backup.json
./scripts/restore-test.sh --manifest /independent-storage/opentofu-state/<backup>.manifest.json --output /protected/evidence/restore.json
./scripts/assemble-evidence.py --git-sha "$LIQI_SOURCE_GIT_SHA" --locking /protected/evidence/locking.json --backup /protected/evidence/backup.json --restore /protected/evidence/restore.json --output /protected/evidence/state-backend-evidence-v1.json
```

A live plan is forbidden until the assembled evidence validates and is bound to the exact source SHA.
