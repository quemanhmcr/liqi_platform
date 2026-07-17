#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PSQL=${PSQL:-psql}
TEST_DATABASE=${LIQI_TEST_DATABASE:-liqi_v0_test}

if [[ ! "$TEST_DATABASE" =~ ^liqi_v0_test(_[a-z0-9_]+)?$ ]]; then
  echo "unsafe test database name: $TEST_DATABASE" >&2
  exit 64
fi

"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --file="$ROOT_DIR/database/bootstrap/00_cluster_roles.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set=database_name="$TEST_DATABASE" --file="$ROOT_DIR/database/bootstrap/10_create_database.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set=database_name="$TEST_DATABASE" --file="$ROOT_DIR/database/bootstrap/20_role_settings.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname="$TEST_DATABASE" -c 'CREATE EXTENSION IF NOT EXISTS pgtap'

PGDATABASE="$TEST_DATABASE" "$ROOT_DIR/database/bin/migrate.sh"
printf '{"bootstrap":"database-test-v0","database":"%s","passed":true}\n' "$TEST_DATABASE"
