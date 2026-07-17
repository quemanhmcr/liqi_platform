#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
temporary=$(mktemp)
trap 'rm -f "$temporary"' EXIT
"$PSQL" --no-psqlrc --csv --quiet --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-127.0.0.1}" --port="${PGPORT:-6432}" \
  --username="${PGUSER:-liqi_monitor}" --dbname=pgbouncer \
  -c 'SHOW POOLS' > "$temporary"
PYTHONDONTWRITEBYTECODE=1 python - "$temporary" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
server = sum(int(row.get(name, 0) or 0) for row in rows for name in ("sv_active", "sv_idle", "sv_used", "sv_tested", "sv_login"))
waiting = sum(int(row.get("cl_waiting", 0) or 0) + int(row.get("cl_waiting_cancel_req", 0) or 0) for row in rows)
print(f"liqi_database_pgbouncer_server_connections {server}")
print(f"liqi_database_pgbouncer_waiting_clients {waiting}")
PY
