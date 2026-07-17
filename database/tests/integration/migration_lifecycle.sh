#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PSQL=${PSQL:-psql}

"$ROOT_DIR/database/bin/migrate.sh" >/dev/null
before=$("$PSQL" --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  -c "SELECT count(*)::text || ':' || max(version)::text FROM platform.schema_migrations")
"$ROOT_DIR/database/bin/migrate.sh" >/dev/null
after=$("$PSQL" --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  -c "SELECT count(*)::text || ':' || max(version)::text FROM platform.schema_migrations")

if [[ "$before" != "$after" ]]; then
  echo "migration rerun changed applied state: before=$before after=$after" >&2
  exit 1
fi
printf '{"test":"migration-rerun","state":"%s","passed":true}\n' "$after"
