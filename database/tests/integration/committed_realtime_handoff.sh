#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
probe_id=00000000-0000-4000-8000-000000000006
event_id=00000000-0000-4000-8000-000000000106
writer_app=liqi-committed-handoff-writer-v0
writer_log=$(mktemp)
cleanup() {
  rm -f "$writer_log"
}
trap cleanup EXIT

after_id=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  -c 'SELECT COALESCE(MAX(handoff_id), 0) FROM platform.realtime_handoff_events_v0')

PGAPPNAME="$writer_app" "$PSQL" --no-psqlrc --quiet --set=ON_ERROR_STOP=1 >"$writer_log" 2>&1 <<SQL &
BEGIN;
SET ROLE liqi_api;
SELECT platform.request_probe_v0('$probe_id', '$event_id', clock_timestamp());
SELECT pg_sleep(10);
COMMIT;
SQL
writer_pid=$!

ready=0
for _ in {1..40}; do
  active=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
    --set=writer_app="$writer_app" \
    -c "SELECT count(*) FROM pg_stat_activity WHERE application_name = :'writer_app' AND state = 'active' AND query LIKE '%pg_sleep%'")
  if [[ "$active" == "1" ]]; then ready=1; break; fi
  sleep 0.1
done
if [[ "$ready" != "1" ]]; then
  wait "$writer_pid" || true
  cat "$writer_log" >&2
  echo 'writer transaction did not reach pre-commit hold' >&2
  exit 1
fi

before_commit=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --set=after_id="$after_id" --set=event_id="$event_id" <<'SQL'
SET ROLE liqi_realtime;
SELECT count(*)
FROM platform.read_realtime_handoff_v0(:'after_id'::bigint, 128)
WHERE event_id = :'event_id'::uuid;
SQL
)
if [[ "$before_commit" != "0" ]]; then
  echo "uncommitted realtime handoff became visible: $before_commit" >&2
  exit 1
fi

wait "$writer_pid" || { cat "$writer_log" >&2; exit 1; }
after_commit=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --set=after_id="$after_id" --set=event_id="$event_id" <<'SQL'
SET ROLE liqi_realtime;
SELECT count(*)
FROM platform.read_realtime_handoff_v0(:'after_id'::bigint, 128)
WHERE event_id = :'event_id'::uuid;
SQL
)
if [[ "$after_commit" != "1" ]]; then
  echo "committed realtime handoff was not visible exactly once: $after_commit" >&2
  exit 1
fi
printf '%s\n' '{"test":"committed-realtime-handoff-v0","beforeCommit":0,"afterCommit":1,"passed":true}'
