#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-127.0.0.1}" --port="${PGPORT:-6432}" \
  --username="${PGUSER:-liqi_monitor}" --dbname="${PGDATABASE:-liqi}" <<'SQL'
SELECT 'liqi_database_outbox_pending ' || pending_count FROM platform.outbox_health_v1
UNION ALL SELECT 'liqi_database_outbox_processing ' || processing_count FROM platform.outbox_health_v1
UNION ALL SELECT 'liqi_database_outbox_dead_letter ' || dead_letter_count FROM platform.outbox_health_v1
UNION ALL SELECT 'liqi_database_outbox_oldest_pending_seconds ' || oldest_pending_seconds FROM platform.outbox_health_v1
UNION ALL SELECT 'liqi_database_realtime_handoff_last_cursor ' || last_handoff_id FROM platform.realtime_handoff_health_v1
UNION ALL SELECT 'liqi_database_realtime_handoff_retained_after_cursor ' || retained_after_handoff_id FROM platform.realtime_handoff_health_v1
UNION ALL SELECT 'liqi_database_realtime_handoff_retained_count ' || retained_count FROM platform.realtime_handoff_health_v1
UNION ALL SELECT 'liqi_database_oban_available ' || available_count FROM platform.oban_health_v1
UNION ALL SELECT 'liqi_database_oban_scheduled ' || scheduled_count FROM platform.oban_health_v1
UNION ALL SELECT 'liqi_database_oban_retryable ' || retryable_count FROM platform.oban_health_v1
UNION ALL SELECT 'liqi_database_oban_executing ' || executing_count FROM platform.oban_health_v1
UNION ALL SELECT 'liqi_database_oban_discarded ' || discarded_count FROM platform.oban_health_v1
UNION ALL SELECT 'liqi_database_oban_oldest_runnable_seconds ' || oldest_runnable_seconds FROM platform.oban_health_v1;
SQL
