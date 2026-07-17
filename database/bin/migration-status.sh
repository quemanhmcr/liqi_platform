#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
: "${PGDATABASE:=liqi}"
export PGDATABASE
required_version=${LIQI_REQUIRED_MIGRATION_VERSION:-0}

"$PSQL" --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 --set=required_version="$required_version" <<'SQL'
SELECT json_build_object(
    'contractVersion', 'database-v0',
    'database', current_database(),
    'currentVersion', readiness.current_version,
    'requiredVersion', readiness.expected_version,
    'ready', readiness.ready,
    'reason', readiness.reason,
    'observedAt', clock_timestamp()
)::text
FROM platform.database_readiness_v0(:'required_version'::bigint) readiness;
SQL
