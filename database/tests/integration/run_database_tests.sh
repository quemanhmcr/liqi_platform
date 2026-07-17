#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
TEST_DATABASE=${LIQI_TEST_DATABASE:-liqi_v0_test}
PG_PROVE=${PG_PROVE:-pg_prove}

command -v "$PG_PROVE" >/dev/null 2>&1 || { echo "required command missing: $PG_PROVE" >&2; exit 69; }

"$ROOT_DIR/database/tests/integration/bootstrap_test_database.sh"
PGDATABASE="$TEST_DATABASE" "$PG_PROVE" --ext .sql "$ROOT_DIR"/database/tests/pgtap/*.sql
PGDATABASE="$TEST_DATABASE" "$ROOT_DIR/database/tests/integration/migration_lifecycle.sh"
PGDATABASE="$TEST_DATABASE" "$ROOT_DIR/database/tests/integration/concurrent_migration_lock.sh"
PGDATABASE="$TEST_DATABASE" "$ROOT_DIR/database/tests/integration/concurrent_outbox_claim.sh"
printf '{"validation":"database-integration-v0","database":"%s","passed":true}\n' "$TEST_DATABASE"
