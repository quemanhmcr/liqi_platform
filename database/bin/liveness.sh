#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
output=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-/run/postgresql}" --port="${PGPORT:-5432}" \
  --username="${PGUSER:-postgres}" --dbname="${PGDATABASE:-liqi}" \
  -c "SELECT json_build_object('schemaVersion','database-liveness-v0','live',true,'database',current_database(),'observedAt',clock_timestamp())::text")
printf '%s\n' "$output"
