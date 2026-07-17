#!/usr/bin/env bash
set -euo pipefail
umask 077

PGBACKREST_BIN=${PGBACKREST_BIN:-/usr/bin/pgbackrest}
CONFIG=${LIQI_PGBACKREST_CONFIG_PATH:-/etc/pgbackrest/pgbackrest.conf}
S3_KEY_FILE=${LIQI_SECRET_PGBACKREST_S3_KEY_FILE:-/run/liqi/secrets/database/backup-s3-key}
S3_SECRET_FILE=${LIQI_SECRET_PGBACKREST_S3_SECRET_FILE:-/run/liqi/secrets/database/backup-s3-secret}
CIPHER_FILE=${LIQI_SECRET_PGBACKREST_CIPHER_FILE:-/run/liqi/secrets/database/backup-cipher-passphrase}

read_secret() {
  local path=$1
  local label=$2
  local target=$3
  [[ -f "$path" && -r "$path" ]] || { echo "required $label credential is unavailable" >&2; exit 78; }
  if find "$path" -maxdepth 0 -perm /077 -print -quit | grep -q .; then
    echo "$label credential permissions are too broad" >&2
    exit 78
  fi
  local lines
  lines=$(awk 'END {print NR}' "$path")
  [[ "$lines" -eq 1 ]] || { echo "$label credential must contain exactly one line" >&2; exit 78; }
  local value
  IFS= read -r value < "$path" || true
  [[ -n "$value" ]] || { echo "$label credential is empty" >&2; exit 78; }
  printf -v "$target" '%s' "$value"
}

command -v "$PGBACKREST_BIN" >/dev/null 2>&1 || { echo "pgBackRest binary unavailable" >&2; exit 69; }
[[ -r "$CONFIG" ]] || { echo "pgBackRest configuration unavailable" >&2; exit 78; }

read_secret "$S3_KEY_FILE" "S3 access key" s3_key
read_secret "$S3_SECRET_FILE" "S3 secret key" s3_secret
read_secret "$CIPHER_FILE" "repository cipher passphrase" cipher_pass

export PGBACKREST_REPO1_S3_KEY="$s3_key"
export PGBACKREST_REPO1_S3_KEY_SECRET="$s3_secret"
export PGBACKREST_REPO1_CIPHER_PASS="$cipher_pass"
unset s3_key s3_secret cipher_pass

exec "$PGBACKREST_BIN" --config="$CONFIG" "$@"
