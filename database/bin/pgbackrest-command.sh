#!/usr/bin/env bash
set -euo pipefail
umask 077
PGBACKREST_BIN=${PGBACKREST_BIN:-/usr/bin/pgbackrest}
CONFIG=${LIQI_PGBACKREST_CONFIG_PATH:-/etc/pgbackrest/pgbackrest.conf}
CA_FILE=${LIQI_SECRET_PGBACKREST_REPO_CA_FILE:-/run/liqi/secrets/database/pgbackrest-repo-ca}
CERT_FILE=${LIQI_SECRET_PGBACKREST_REPO_CLIENT_CERT_FILE:-/run/liqi/secrets/database/pgbackrest-repo-client-cert}
KEY_FILE=${LIQI_SECRET_PGBACKREST_REPO_CLIENT_KEY_FILE:-/run/liqi/secrets/database/pgbackrest-repo-client-key}
CIPHER_FILE=${LIQI_SECRET_PGBACKREST_CIPHER_FILE:-/run/liqi/secrets/database/pgbackrest-cipher-passphrase}
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 78; }
secure_file(){ local p=$1 label=$2; [[ -f "$p" && ! -L "$p" && -r "$p" ]] || fail "$label file is unavailable"; find "$p" -maxdepth 0 -perm /077 -print -quit | grep -q . && fail "$label permissions are too broad"; [[ $(stat -c '%s' "$p") -gt 0 && $(stat -c '%s' "$p") -le 65536 ]] || fail "$label size is invalid"; }
command -v "$PGBACKREST_BIN" >/dev/null 2>&1 || { echo 'pgBackRest binary unavailable' >&2; exit 69; }
[[ -r "$CONFIG" ]] || fail 'pgBackRest configuration unavailable'
secure_file "$CA_FILE" 'repository CA'; secure_file "$CERT_FILE" 'repository client certificate'; secure_file "$KEY_FILE" 'repository client private key'; secure_file "$CIPHER_FILE" 'repository cipher passphrase'
lines=$(awk 'END {print NR}' "$CIPHER_FILE"); [[ "$lines" -eq 1 ]] || fail 'repository cipher passphrase must contain exactly one line'
IFS= read -r cipher_pass < "$CIPHER_FILE" || true; [[ ${#cipher_pass} -ge 24 ]] || fail 'repository cipher passphrase is too short'
export PGBACKREST_REPO1_HOST_CA_FILE="$CA_FILE"
export PGBACKREST_REPO1_HOST_CERT_FILE="$CERT_FILE"
export PGBACKREST_REPO1_HOST_KEY_FILE="$KEY_FILE"
export PGBACKREST_REPO1_CIPHER_PASS="$cipher_pass"
unset cipher_pass
exec "$PGBACKREST_BIN" --config="$CONFIG" "$@"
