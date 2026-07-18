#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PSQL=${PSQL:-psql}
UPGRADE_DATABASE=${LIQI_V0_UPGRADE_DATABASE:-liqi_v0_upgrade_test}
if [[ ! "$UPGRADE_DATABASE" =~ ^liqi_v0_upgrade_test(_[a-z0-9_]+)?$ ]]; then
  echo "unsafe V0 upgrade database name: $UPGRADE_DATABASE" >&2
  exit 64
fi
v0_dir=$(mktemp -d)
cleanup() {
  rm -rf "$v0_dir"
  "$PSQL" --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --dbname=postgres \
    --set=database_name="$UPGRADE_DATABASE" <<'SQL' >/dev/null 2>&1 || true
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'database_name' AND pid <> pg_backend_pid();
SELECT format('DROP DATABASE IF EXISTS %I', :'database_name') \gexec
SQL
}
trap cleanup EXIT

"$PSQL" --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --dbname=postgres \
  --set=database_name="$UPGRADE_DATABASE" <<'SQL'
SELECT format('DROP DATABASE IF EXISTS %I', :'database_name') \gexec
SQL
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --file="$ROOT_DIR/database/bootstrap/00_cluster_roles.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set=database_name="$UPGRADE_DATABASE" --file="$ROOT_DIR/database/bootstrap/10_create_database.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set=database_name="$UPGRADE_DATABASE" --file="$ROOT_DIR/database/bootstrap/20_role_settings.sql"

cp "$ROOT_DIR"/database/migrations/00000000000{1,2,3,4}_*.sql "$v0_dir/"
grep -E '00000000000[1-4]_' "$ROOT_DIR/database/migrations/manifest.sha256" > "$v0_dir/manifest.sha256"
PGDATABASE="$UPGRADE_DATABASE" LIQI_MIGRATION_DIR="$v0_dir" LIQI_MIGRATION_MANIFEST="$v0_dir/manifest.sha256" \
  "$ROOT_DIR/database/bin/migrate.sh" >/dev/null

v0_state=$(PGDATABASE="$UPGRADE_DATABASE" "$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 <<'SQL'
SELECT platform.current_migration_version_v0()::text
  || ':' || to_regprocedure('platform.request_probe_v0(uuid,uuid,timestamptz,uuid,uuid,jsonb)')::text
  || ':' || to_regprocedure('platform.read_realtime_handoff_v0(bigint,integer)')::text;
SQL
)
if [[ "$v0_state" != "4:platform.request_probe_v0(uuid,uuid,timestamp with time zone,uuid,uuid,jsonb):platform.read_realtime_handoff_v0(bigint,integer)" ]]; then
  echo "V0 fixture is not the final migration-4 schema: $v0_state" >&2
  exit 1
fi

PGDATABASE="$UPGRADE_DATABASE" "$ROOT_DIR/database/bin/migrate.sh" >/dev/null
upgrade_state=$(PGDATABASE="$UPGRADE_DATABASE" "$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 <<'SQL'
SELECT platform.current_migration_version_v0()::text
  || ':' || platform.current_oban_migration_version_v1()::text
  || ':' || (SELECT ready::text FROM platform.database_readiness_v1(8, 14))
  || ':' || (to_regprocedure('platform.request_probe_v0(uuid,uuid,timestamptz,uuid,uuid,jsonb)') IS NOT NULL)::text
  || ':' || (to_regprocedure('platform.read_realtime_handoff_v0(bigint,integer)') IS NOT NULL)::text;
SQL
)
if [[ "$upgrade_state" != "8:14:true:true:true" ]]; then
  echo "V0 to V1 compatibility failed: $upgrade_state" >&2
  exit 1
fi
printf '%s\n' '{"test":"v0-upgrade-compatibility-v1","fromMigration":4,"toMigration":8,"v0FunctionsRetained":true,"passed":true}'
