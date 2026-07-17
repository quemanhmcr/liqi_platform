#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
: "${LIQI_REQUIRED_MIGRATION_VERSION:=0}"
"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-/run/postgresql}" --port="${PGPORT:-5432}" \
  --username="${PGUSER:-postgres}" --dbname="${PGDATABASE:-liqi}" \
  --set=required_version="$LIQI_REQUIRED_MIGRATION_VERSION" <<'SQL'
SET ROLE liqi_monitor;
WITH readiness AS (
    SELECT * FROM platform.database_readiness_v0(:'required_version'::bigint)
), connections AS (
    SELECT count(*)::double precision AS used,
           current_setting('max_connections')::double precision AS maximum
    FROM pg_stat_activity
), outbox AS (
    SELECT * FROM platform.outbox_health_v0
), archive AS (
    SELECT
        failed_count::double precision AS failed_count,
        CASE WHEN last_archived_time IS NULL THEN -1::double precision
             ELSE EXTRACT(EPOCH FROM (clock_timestamp() - last_archived_time))::double precision END AS age
    FROM pg_stat_archiver
)
SELECT line
FROM (
    SELECT 1 AS ordering, 'liqi_database_up 1' AS line
    UNION ALL SELECT 2, format('liqi_database_ready %s', CASE WHEN readiness.ready THEN 1 ELSE 0 END) FROM readiness
    UNION ALL SELECT 3, format('liqi_database_connections %s', connections.used) FROM connections
    UNION ALL SELECT 4, format('liqi_database_connection_saturation_ratio %s', connections.used / NULLIF(connections.maximum, 0)) FROM connections
    UNION ALL SELECT 5, format('liqi_database_migration_version %s', readiness.current_version) FROM readiness
    UNION ALL SELECT 6, format('liqi_database_outbox_pending %s', outbox.pending_count) FROM outbox
    UNION ALL SELECT 7, format('liqi_database_outbox_oldest_pending_seconds %s', outbox.oldest_pending_seconds) FROM outbox
    UNION ALL SELECT 8, format('liqi_database_outbox_dead_letter %s', outbox.dead_letter_count) FROM outbox
    UNION ALL SELECT 9, format('liqi_database_wal_archive_age_seconds %s', archive.age) FROM archive
    UNION ALL SELECT 10, format('liqi_database_wal_archive_failures_total %s', archive.failed_count) FROM archive
) metrics
ORDER BY ordering;
RESET ROLE;
SQL
