#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${LIQI_RESTORE_METADATA_FILE:?LIQI_RESTORE_METADATA_FILE is required}"
: "${LIQI_RESTORE_METADATA_CHECKSUM_FILE:?LIQI_RESTORE_METADATA_CHECKSUM_FILE is required}"
: "${LIQI_RESTORE_TARGET_PGDATA:?LIQI_RESTORE_TARGET_PGDATA is required}"

PGBACKREST_COMMAND=${LIQI_PGBACKREST_COMMAND:-"$ROOT_DIR/database/bin/pgbackrest-command.sh"}
PG_CTL=${PG_CTL:-pg_ctl}
RESTORE_ROOT=${LIQI_RESTORE_ROOT:-/var/lib/liqi/postgresql/backup-staging/restore}
SOCKET_DIR=${LIQI_RESTORE_SOCKET_DIR:-/run/liqi/restore/$(basename "$LIQI_RESTORE_TARGET_PGDATA")}
PORT=${LIQI_RESTORE_PORT:-55432}
RESULT_FILE=${LIQI_RESTORE_RESULT_FILE:-"$LIQI_RESTORE_TARGET_PGDATA/restore-result.json"}
KEEP_RUNNING=${LIQI_KEEP_RESTORE_RUNNING:-false}
SOURCE_PGDATA=${LIQI_PGDATA:-/var/lib/liqi/postgresql/data}
RESTORE_LOCK_FILE=${LIQI_RESTORE_LOCK_FILE:-/run/lock/liqi-database-restore.lock}
EVIDENCE_METADATA_DIR=${LIQI_BACKUP_METADATA_DIR:-/var/lib/liqi/postgresql/backup-staging/metadata}

for command_name in "$PG_CTL" psql python sha256sum flock; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "required command missing: $command_name" >&2; exit 69; }
done
[[ -x "$PGBACKREST_COMMAND" ]] || { echo 'pgBackRest command boundary unavailable' >&2; exit 69; }
mkdir -p "$(dirname "$RESTORE_LOCK_FILE")"
exec 9>"$RESTORE_LOCK_FILE"
flock -n 9 || { echo 'another database restore is already running' >&2; exit 75; }
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || { echo 'restore port must be between 1024 and 65535' >&2; exit 64; }

canonical_target=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$LIQI_RESTORE_TARGET_PGDATA")
canonical_root=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$RESTORE_ROOT")
canonical_source=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SOURCE_PGDATA")
[[ "$canonical_target" == "$canonical_root"/* ]] || { echo 'restore target must be below LIQI_RESTORE_ROOT' >&2; exit 64; }
[[ "$canonical_target" != "$canonical_source" ]] || { echo 'in-place restore is forbidden' >&2; exit 64; }
[[ "$canonical_target" != / && "$canonical_target" != /var && "$canonical_target" != /var/lib ]] || { echo 'unsafe restore target' >&2; exit 64; }
if [[ -e "$canonical_target" ]] && find "$canonical_target" -mindepth 1 -print -quit | grep -q .; then
  echo 'restore target must not exist or must be empty' >&2
  exit 65
fi

PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-metadata \
  --metadata "$LIQI_RESTORE_METADATA_FILE" \
  --checksum "$LIQI_RESTORE_METADATA_CHECKSUM_FILE" >/dev/null
label=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["backup"]["label"])' "$LIQI_RESTORE_METADATA_FILE")

# Preserve the exact restored backup metadata under its immutable label.
mkdir -p "$EVIDENCE_METADATA_DIR"
chmod 700 "$EVIDENCE_METADATA_DIR"
source_metadata="$EVIDENCE_METADATA_DIR/$label.json"
source_checksum="$source_metadata.sha256"
input_sha=$(sha256sum "$LIQI_RESTORE_METADATA_FILE" | awk '{print $1}')
if [[ -e "$source_metadata" || -e "$source_checksum" ]]; then
  [[ -r "$source_metadata" && -r "$source_checksum" ]] || { echo 'restore source metadata publication is incomplete' >&2; exit 65; }
  PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-metadata \
    --metadata "$source_metadata" --checksum "$source_checksum" >/dev/null
  existing_sha=$(sha256sum "$source_metadata" | awk '{print $1}')
  [[ "$existing_sha" == "$input_sha" ]] || { echo 'restore source label already exists with different metadata' >&2; exit 65; }
else
  source_metadata_tmp="$EVIDENCE_METADATA_DIR/.$label.$$.json"
  source_checksum_tmp="$source_metadata_tmp.sha256"
  cp "$LIQI_RESTORE_METADATA_FILE" "$source_metadata_tmp"
  printf '%s  %s\n' "$input_sha" "$(basename "$source_metadata")" > "$source_checksum_tmp"
  mv "$source_metadata_tmp" "$source_metadata"
  mv "$source_checksum_tmp" "$source_checksum"
fi

restore_id=$(PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
import uuid
print(uuid.uuid4())
PY
)
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

mkdir -p "$canonical_target" "$SOCKET_DIR" "$(dirname "$RESULT_FILE")"
chmod 700 "$canonical_target" "$SOCKET_DIR"
restore_args=(
  --stanza=liqi
  --set="$label"
  --pg1-path="$canonical_target"
  --archive-mode=off
  --target-action=promote
  --target-timeline=latest
)
if [[ -n "${LIQI_RESTORE_TARGET_TIME:-}" ]]; then
  restore_args+=(--type=time --target="$LIQI_RESTORE_TARGET_TIME")
else
  restore_args+=(--type=default)
fi
restore_args+=(restore)
"$PGBACKREST_COMMAND" "${restore_args[@]}"

cat > "$canonical_target/pg_hba.conf" <<'EOF'
local all postgres peer
local all all reject
host all all 0.0.0.0/0 reject
host all all ::0/0 reject
EOF
chmod 600 "$canonical_target/pg_hba.conf"
cat >> "$canonical_target/postgresql.auto.conf" <<EOF
# LIQI isolated restore drill overrides
listen_addresses = ''
port = '$PORT'
unix_socket_directories = '$SOCKET_DIR'
archive_mode = 'off'
logging_collector = 'off'
EOF

restore_log="$canonical_target/restore-postgresql.log"
running=false
cleanup() {
  if [[ "$running" == true && "$KEEP_RUNNING" != true ]]; then
    "$PG_CTL" -D "$canonical_target" -m fast -w stop >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
"$PG_CTL" -D "$canonical_target" -l "$restore_log" -t 900 -w start
running=true

LIQI_RESTORE_ID="$restore_id" \
LIQI_RESTORE_STARTED_AT="$started_at" \
LIQI_RESTORE_TARGET_PGDATA="$canonical_target" \
LIQI_RESTORE_SOCKET_DIR="$SOCKET_DIR" \
LIQI_RESTORE_PORT="$PORT" \
LIQI_RESTORE_RESULT_FILE="$RESULT_FILE" \
LIQI_RESTORE_METADATA_FILE="$LIQI_RESTORE_METADATA_FILE" \
LIQI_RESTORE_METADATA_CHECKSUM_FILE="$LIQI_RESTORE_METADATA_CHECKSUM_FILE" \
  "$ROOT_DIR/database/recovery/verify-restore.sh"

if [[ "$KEEP_RUNNING" != true ]]; then
  "$PG_CTL" -D "$canonical_target" -m fast -w stop
  running=false
fi

# Publish local, checksummed latest restore evidence atomically after verification.
LATEST_EVIDENCE_DIR=${LIQI_RESTORE_LATEST_EVIDENCE_DIR:-"$RESTORE_ROOT/latest"}
mkdir -p "$LATEST_EVIDENCE_DIR"
chmod 700 "$LATEST_EVIDENCE_DIR"
latest_result="$LATEST_EVIDENCE_DIR/restore-result.json"
latest_checksum="$latest_result.sha256"
latest_result_tmp="$LATEST_EVIDENCE_DIR/.restore-result.$restore_id.json"
latest_checksum_tmp="$latest_result_tmp.sha256"
cp "$RESULT_FILE" "$latest_result_tmp"
result_sha=$(sha256sum "$latest_result_tmp" | awk '{print $1}')
printf '%s  %s\n' "$result_sha" "$(basename "$latest_result")" > "$latest_checksum_tmp"
mv -f "$latest_result_tmp" "$latest_result"
mv -f "$latest_checksum_tmp" "$latest_checksum"

cat "$RESULT_FILE"
