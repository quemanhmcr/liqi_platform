#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${LIQI_RESTORE_METADATA_FILE:?LIQI_RESTORE_METADATA_FILE is required}"
: "${LIQI_RESTORE_METADATA_CHECKSUM_FILE:?LIQI_RESTORE_METADATA_CHECKSUM_FILE is required}"
: "${LIQI_RESTORE_RESULT_FILE:?LIQI_RESTORE_RESULT_FILE is required}"
: "${LIQI_RESTORE_ID:?LIQI_RESTORE_ID is required}"
: "${LIQI_RESTORE_STARTED_AT:?LIQI_RESTORE_STARTED_AT is required}"
: "${LIQI_RESTORE_TARGET_PGDATA:?LIQI_RESTORE_TARGET_PGDATA is required}"
: "${LIQI_RESTORE_SOCKET_DIR:?LIQI_RESTORE_SOCKET_DIR is required}"
: "${LIQI_RESTORE_PORT:?LIQI_RESTORE_PORT is required}"

PSQL=${PSQL:-psql}
command -v "$PSQL" >/dev/null 2>&1 || { echo 'psql unavailable' >&2; exit 69; }
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-metadata \
  --metadata "$LIQI_RESTORE_METADATA_FILE" \
  --checksum "$LIQI_RESTORE_METADATA_CHECKSUM_FILE" >/dev/null

probe_id=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["probe"]["probeId"])' "$LIQI_RESTORE_METADATA_FILE")
event_id=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["probe"]["eventId"])' "$LIQI_RESTORE_METADATA_FILE")
actual=$(mktemp)
trap 'rm -f "$actual"' EXIT

PGHOST="$LIQI_RESTORE_SOCKET_DIR" PGPORT="$LIQI_RESTORE_PORT" PGUSER=postgres PGDATABASE=liqi \
"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --set=probe_id="$probe_id" --set=event_id="$event_id" > "$actual" <<'SQL'
SELECT json_build_object(
    'postgresqlMajor', current_setting('server_version_num')::integer / 10000,
    'postgresqlVersion', current_setting('server_version'),
    'migrationVersion', platform.current_migration_version_v0(),
    'failedMigrationRuns', (SELECT count(*) FROM platform.migration_runs WHERE status = 'failed'),
    'inRecovery', pg_is_in_recovery(),
    'archiveMode', current_setting('archive_mode'),
    'listenAddresses', current_setting('listen_addresses'),
    'migrations', (
        SELECT json_agg(json_build_object(
            'version', version,
            'name', name,
            'checksumSha256', checksum_sha256
        ) ORDER BY version)
        FROM platform.schema_migrations
    ),
    'probe', (
        SELECT json_build_object(
            'probeId', state.probe_id,
            'eventId', state.requested_event_id,
            'probeStatus', state.status,
            'outboxState', event.state,
            'effectCount', count(effect.event_id)
        )
        FROM platform.probe_state_v0 state
        JOIN platform.outbox_events event ON event.event_id = state.requested_event_id
        LEFT JOIN platform.probe_effects_v0 effect ON effect.event_id = state.requested_event_id
        WHERE state.probe_id = :'probe_id'::uuid
          AND state.requested_event_id = :'event_id'::uuid
        GROUP BY state.probe_id, state.requested_event_id, state.status, event.state
    )
)::text;
SQL

PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" verify-restore \
  --metadata "$LIQI_RESTORE_METADATA_FILE" \
  --checksum "$LIQI_RESTORE_METADATA_CHECKSUM_FILE" \
  --actual "$actual" \
  --manifest "$ROOT_DIR/database/migrations/manifest.sha256" \
  --restore-id "$LIQI_RESTORE_ID" \
  --started-at "$LIQI_RESTORE_STARTED_AT" \
  --target-pgdata "$LIQI_RESTORE_TARGET_PGDATA" \
  --socket-directory "$LIQI_RESTORE_SOCKET_DIR" \
  --port "$LIQI_RESTORE_PORT" \
  --output "$LIQI_RESTORE_RESULT_FILE"
