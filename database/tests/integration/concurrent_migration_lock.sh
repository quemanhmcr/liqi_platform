#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
PSQL=${PSQL:-psql}
holder_app=liqi-migration-lock-holder-v1
lock_output=$(mktemp)
migrate_output=$(mktemp)
cleanup() {
  rm -f "$lock_output" "$migrate_output"
}
trap cleanup EXIT

PGAPPNAME="$holder_app" "$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 >"$lock_output" 2>&1 <<SQL &
SELECT pg_advisory_lock(hashtextextended('liqi_platform:migrations:v0', 0));
SELECT pg_sleep(2);
SELECT pg_advisory_unlock(hashtextextended('liqi_platform:migrations:v0', 0));
SQL
holder_pid=$!

ready=0
for _ in {1..40}; do
  active=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
    -c "SELECT count(*) FROM pg_stat_activity WHERE application_name = '$holder_app' AND state = 'active' AND query LIKE '%pg_sleep%'")
  if [[ "$active" == "1" ]]; then
    ready=1
    break
  fi
  sleep 0.05
done
if [[ "$ready" != "1" ]]; then
  wait "$holder_pid" || true
  cat "$lock_output" >&2
  echo 'lock holder did not become ready' >&2
  exit 1
fi

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
wait "$holder_pid" || { cat "$lock_output" >&2; exit 1; }

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
