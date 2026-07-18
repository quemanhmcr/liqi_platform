#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
probe_id=00000000-0000-4000-8000-000000000002
event_id=00000000-0000-4000-8000-000000000102

cleanup() {
  "$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --quiet <<SQL >/dev/null
DELETE FROM platform.outbox_attempts WHERE event_id = '$event_id';
DELETE FROM platform.probe_effects_v0 WHERE event_id = '$event_id';
DELETE FROM platform.realtime_handoff_events_v0 WHERE event_id = '$event_id';
DELETE FROM platform.probe_state_v0 WHERE probe_id = '$probe_id';
DELETE FROM platform.outbox_events WHERE event_id = '$event_id';
SQL
}
cleanup

"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --quiet <<SQL
SELECT platform.request_probe_v0('$probe_id', '$event_id', clock_timestamp());
SQL

first=$(mktemp)
second=$(mktemp)
trap 'rm -f "$first" "$second"' EXIT

"$PSQL" --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 >"$first" <<'SQL' &
BEGIN;
SELECT event_id FROM platform.claim_outbox_v0('claimant-a', 1, 30);
SELECT pg_sleep(2);
COMMIT;
SQL
pid_a=$!
sleep 0.25
"$PSQL" --no-psqlrc --tuples-only --no-align --set=ON_ERROR_STOP=1 >"$second" <<'SQL' &
SELECT event_id FROM platform.claim_outbox_v0('claimant-b', 1, 30);
SQL
pid_b=$!
wait "$pid_a"
wait "$pid_b"

claimed_count=$(cat "$first" "$second" | grep -c "$event_id" || true)
if [[ "$claimed_count" -ne 1 ]]; then
  echo "expected one concurrent claim, observed $claimed_count" >&2
  exit 1
fi
cleanup
printf '{"test":"concurrent-outbox-claim","eventId":"%s","claimCount":1,"passed":true}\n' "$event_id"
