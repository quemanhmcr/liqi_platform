#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PSQL=${PSQL:-psql}

# Run through the local administrative Unix socket. No password or DSN is accepted.
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --file="$ROOT_DIR/database/bootstrap/00_cluster_roles.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set=database_name=liqi --file="$ROOT_DIR/database/bootstrap/10_create_database.sql"
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set=database_name=liqi --file="$ROOT_DIR/database/bootstrap/20_role_settings.sql"

echo 'cluster bootstrap complete; role passwords remain externally materialized secrets'
