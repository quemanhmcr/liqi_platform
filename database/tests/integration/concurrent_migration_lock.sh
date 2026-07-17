#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PSQL=${PSQL:-psql}
lock_ready=$(mktemp)
lock_output=$(mktemp)
migrate_output=$(mktemp)
trap 'rm -f "$lock_ready" "$lock_output" "$migrate_output"' EXIT

"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 >"$lock_output" <<SQL &
SELECT pg_advisory_lock(hashtextextended('liqi_platform:migrations:v0', 0));
\! printf ready > '$lock_ready'
SELECT pg_sleep(2);
SELECT pg_advisory_unlock(hashtextextended('liqi_platform:migrations:v0', 0));
SQL
holder_pid=$!
for _ in {1..40}; do
  [[ -s "$lock_ready" ]] && break
  sleep 0.05
done
[[ -s "$lock_ready" ]] || { echo 'lock holder did not become ready' >&2; exit 1; }

start=$(python - <<'PY'
import time
print(time.monotonic())
PY
)
"$ROOT_DIR/database/bin/migrate.sh" >"$migrate_output"
end=$(python - <<'PY'
import time
print(time.monotonic())
PY
)
wait "$holder_pid"

elapsed=$(python - "$start" "$end" <<'PY'
import sys
print(float(sys.argv[2]) - float(sys.argv[1]))
PY
)
python - "$elapsed" <<'PY'
import json, sys
elapsed = float(sys.argv[1])
if elapsed < 1.5:
    raise SystemExit(f"migration runner did not wait for advisory lock: {elapsed:.3f}s")
print(json.dumps({"test":"concurrent-migration-lock","waitSeconds":round(elapsed,3),"passed":True}, separators=(",", ":")))
PY
