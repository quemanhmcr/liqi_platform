#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR=${LIQI_SOURCE_ROOT:-/workspace}
GENERATED_DIR=${LIQI_GENERATED_DIR:-/run/liqi/generated}
SOURCE_REVISION=${LIQI_SOURCE_REVISION:-unknown}
export PGHOST=${PGHOST:-postgres}
export PGPORT=${PGPORT:-5432}
export PGUSER=${PGUSER:-postgres}

for _attempt in $(seq 1 60); do
  pg_isready --quiet && break
  sleep 1
done
pg_isready --quiet || { echo "PostgreSQL did not become ready" >&2; exit 69; }

psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres \
  --file="$ROOT_DIR/database/bootstrap/00_cluster_roles.sql"
psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres \
  --set=database_name=liqi --file="$ROOT_DIR/database/bootstrap/10_create_database.sql"
psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres \
  --set=database_name=liqi --file="$ROOT_DIR/database/bootstrap/20_role_settings.sql"

PGDATABASE=liqi "$ROOT_DIR/database/bin/migrate.sh"

migration_version=$(psql --no-psqlrc --tuples-only --no-align --dbname=liqi \
  --command='SELECT max(version) FROM platform.schema_migrations')
[[ "$migration_version" == "8" ]] || { echo "expected migration 8, got: $migration_version" >&2; exit 65; }

install -d -m 0755 "$GENERATED_DIR"
cat > "$GENERATED_DIR/database-init-result.json" <<JSON
{
  "schema_version": "liqi.local-database-init/v1",
  "source_revision": "$SOURCE_REVISION",
  "database": "liqi",
  "migration_version": 8,
  "authentication_scope": "docker-internal-trust-only",
  "status": "passed"
}
JSON
chmod 0444 "$GENERATED_DIR/database-init-result.json"
printf '%s\n' '{"validation":"local-database-init-v1","migration":8,"status":"passed"}'
