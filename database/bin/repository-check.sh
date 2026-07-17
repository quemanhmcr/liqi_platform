#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LOCK_FILE=${LIQI_BACKUP_LOCK_FILE:-/run/lock/liqi-database-backup.lock}
STATUS_OUTPUT=${LIQI_BACKUP_STATUS_FILE:-/var/lib/liqi/postgresql/backup-staging/metadata/backup-status-v0.json}
PGBACKREST_COMMAND=${LIQI_PGBACKREST_COMMAND:-"$ROOT_DIR/database/bin/pgbackrest-command.sh"}

mkdir -p "$(dirname "$LOCK_FILE")" "$(dirname "$STATUS_OUTPUT")"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo 'database backup or repository operation is already running' >&2; exit 75; }
"$PGBACKREST_COMMAND" --stanza=liqi check
"$ROOT_DIR/database/bin/backup-status.sh" --output "$STATUS_OUTPUT"
