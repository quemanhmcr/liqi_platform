#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
: "${LIQI_REQUIRED_MIGRATION_VERSION:=8}"
: "${LIQI_REQUIRED_OBAN_MIGRATION_VERSION:=14}"
output=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-127.0.0.1}" --port="${PGPORT:-6432}" \
  --username="${PGUSER:-liqi_monitor}" --dbname="${PGDATABASE:-liqi}" \
  --set=required_version="$LIQI_REQUIRED_MIGRATION_VERSION" \
  --set=required_oban_version="$LIQI_REQUIRED_OBAN_MIGRATION_VERSION" <<'SQL'
SELECT json_build_object(
    'schemaVersion', 'migration-readiness-v1',
    'status', CASE WHEN readiness.write_ready THEN 'passed' WHEN readiness.ready THEN 'blocked' ELSE 'failed' END,
    'ready', readiness.ready,
    'writeReady', readiness.write_ready,
    'reason', readiness.reason,
    'currentVersion', readiness.current_version,
    'requiredVersion', readiness.expected_version,
    'obanMigrationVersion', readiness.oban_migration_version,
    'requiredObanMigrationVersion', readiness.expected_oban_migration_version,
    'inRecovery', readiness.in_recovery,
    'observedAt', clock_timestamp()
)::text
FROM platform.database_readiness_v1(
    :'required_version'::bigint,
    :'required_oban_version'::integer
) readiness;
SQL
)
printf '%s\n' "$output"
PYTHONDONTWRITEBYTECODE=1 python - "$output" <<'PY'
import json, sys
value = json.loads(sys.argv[1])
raise SystemExit(0 if value["status"] == "passed" and value["writeReady"] else 1)
PY
