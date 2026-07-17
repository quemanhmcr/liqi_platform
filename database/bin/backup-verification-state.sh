#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
: "${PGDATABASE:=liqi}"
: "${PGHOST:=/run/postgresql}"
: "${PGUSER:=postgres}"
export PGDATABASE PGHOST PGUSER

"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 <<'SQL'
SET ROLE liqi_backup;
SELECT platform.backup_verification_state_v0()::text;
RESET ROLE;
SQL
