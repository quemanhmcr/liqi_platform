#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
: "${LIQI_REQUIRED_MIGRATION_VERSION:?LIQI_REQUIRED_MIGRATION_VERSION is required}"
output=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-127.0.0.1}" --port="${PGPORT:-6432}" \
  --username="${PGUSER:-liqi_monitor}" --dbname="${PGDATABASE:-liqi}" \
  --set=required_version="$LIQI_REQUIRED_MIGRATION_VERSION" <<'SQL'
SELECT json_build_object(
    'schemaVersion', 'database-readiness-v0',
    'ready', readiness.ready,
    'reason', readiness.reason,
    'currentVersion', readiness.current_version,
    'requiredVersion', readiness.expected_version,
    'observedAt', clock_timestamp()
)::text
FROM platform.database_readiness_v0(:'required_version'::bigint) readiness;
SQL
)
printf '%s\n' "$output"
PYTHONDONTWRITEBYTECODE=1 python - "$output" <<'PY'
import json, sys
raise SystemExit(0 if json.loads(sys.argv[1])["ready"] else 1)
PY
