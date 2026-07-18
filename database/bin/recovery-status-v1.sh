#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PSQL=${PSQL:-psql}
: "${LIQI_SOURCE_REVISION:?LIQI_SOURCE_REVISION is required}"
: "${LIQI_RELEASE_ID:?LIQI_RELEASE_ID is required}"
state_file=$(mktemp)
trap 'rm -f "$state_file"' EXIT
"$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-127.0.0.1}" --port="${PGPORT:-6432}" \
  --username="${PGUSER:-liqi_backup}" --dbname="${PGDATABASE:-liqi}" \
  -c 'SELECT platform.backup_verification_state_v1()::text' > "$state_file"
args=(
  --database-state "$state_file"
  --source-revision "$LIQI_SOURCE_REVISION"
  --release-id "$LIQI_RELEASE_ID"
)
if [[ -n "${LIQI_BACKUP_STATUS_FILE:-}" ]]; then
  args+=(--backup-status "$LIQI_BACKUP_STATUS_FILE" --backup-status-checksum "${LIQI_BACKUP_STATUS_CHECKSUM_FILE:-$LIQI_BACKUP_STATUS_FILE.sha256}")
fi
if [[ -n "${LIQI_RESTORE_RESULT_FILE:-}" ]]; then
  args+=(--restore-result "$LIQI_RESTORE_RESULT_FILE" --restore-result-checksum "${LIQI_RESTORE_RESULT_CHECKSUM_FILE:-$LIQI_RESTORE_RESULT_FILE.sha256}")
fi
[[ -z "${LIQI_RESTORED_SOURCE_REVISION:-}" ]] || args+=(--restored-source-revision "$LIQI_RESTORED_SOURCE_REVISION")
[[ -z "${LIQI_RECOVERY_STATUS_OUTPUT:-}" ]] || args+=(--output "$LIQI_RECOVERY_STATUS_OUTPUT")
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_status_v1.py" "${args[@]}"
