#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
probe_id=30000000-0000-4000-8000-000000000001
first_event_id=30000000-0000-4000-8000-000000000101
second_event_id=30000000-0000-4000-8000-000000000102
scope=platform.probe.create.v1
idempotency_key=concurrent-command-001
first=$(mktemp)
second=$(mktemp)
cleanup_files() { rm -f "$first" "$second"; }
cleanup_rows() {
  "$PSQL" --no-psqlrc --quiet --set=ON_ERROR_STOP=1 <<SQL >/dev/null
DELETE FROM platform.outbox_attempts WHERE event_id IN ('$first_event_id', '$second_event_id');
DELETE FROM platform.probe_effects_v0 WHERE event_id IN ('$first_event_id', '$second_event_id');
DELETE FROM platform.realtime_handoff_events_v1 WHERE event_id IN ('$first_event_id', '$second_event_id');
DELETE FROM platform.command_idempotency_v1 WHERE scope = '$scope' AND idempotency_key = '$idempotency_key';
DELETE FROM platform.probe_state_v0 WHERE probe_id = '$probe_id';
DELETE FROM platform.outbox_events WHERE event_id IN ('$first_event_id', '$second_event_id');
SQL
}
trap 'cleanup_files' EXIT
cleanup_rows

"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 >"$first" <<SQL &
BEGIN;
SET ROLE liqi_api;
SELECT duplicate FROM platform.request_probe_v1(
  '$probe_id', '$first_event_id', '$scope', '$idempotency_key', '7777777777777777777777777777777777777777777777777777777777777777', 0
);
SELECT pg_sleep(2);
COMMIT;
SQL
first_pid=$!
sleep 0.25
"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 >"$second" <<SQL &
SET ROLE liqi_api;
SELECT duplicate FROM platform.request_probe_v1(
  '$probe_id', '$second_event_id', '$scope', '$idempotency_key', '7777777777777777777777777777777777777777777777777777777777777777', 0
);
SQL
second_pid=$!
wait "$first_pid"
wait "$second_pid"

first_duplicate=$(grep -E '^[ft]$' "$first" | head -1)
second_duplicate=$(grep -E '^[ft]$' "$second" | head -1)
if [[ "$first_duplicate" != "f" || "$second_duplicate" != "t" ]]; then
  echo "unexpected duplicate outcomes: first=$first_duplicate second=$second_duplicate" >&2
  cat "$first" "$second" >&2
  exit 1
fi

state=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 <<SQL
SELECT
  (SELECT count(*) FROM platform.command_idempotency_v1 WHERE scope = '$scope' AND idempotency_key = '$idempotency_key')::text
  || ':' ||
  (SELECT count(*) FROM platform.outbox_events WHERE event_id IN ('$first_event_id', '$second_event_id'))::text
  || ':' ||
  (SELECT event_id::text FROM platform.command_idempotency_v1 WHERE scope = '$scope' AND idempotency_key = '$idempotency_key');
SQL
)
if [[ "$state" != "1:1:$first_event_id" ]]; then
  echo "concurrent idempotency authority diverged: $state" >&2
  exit 1
fi
cleanup_rows
printf '%s\n' '{"test":"concurrent-idempotency-v1","commands":2,"durableOutcomes":1,"duplicateResponses":1,"passed":true}'
