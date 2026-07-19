#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_identifier "$STATE_DATABASE"
validate_identifier "$STATE_SCHEMA"
validate_identifier "$STATE_ROLE"
: "${STATE_RUNTIME_CREDENTIAL_FILE:?STATE_RUNTIME_CREDENTIAL_FILE is required}"
: "${STATE_ENCRYPTION_PASSPHRASE_FILE:?STATE_ENCRYPTION_PASSPHRASE_FILE is required}"
require_file_0600 "$STATE_RUNTIME_CREDENTIAL_FILE"
require_file_0600 "$STATE_ENCRYPTION_PASSPHRASE_FILE"
if [ -n "${PGPASSFILE:-}" ]; then require_file_0600 "$PGPASSFILE"; fi
[ "$#" -gt 0 ] || fail 'a command is required'

runtime_credential=$(tr -d '\r\n' < "$STATE_RUNTIME_CREDENTIAL_FILE")
encryption_passphrase=$(tr -d '\r\n' < "$STATE_ENCRYPTION_PASSPHRASE_FILE")
[[ "$runtime_credential" =~ ^([0-9a-f]{64}|[0-9a-f]{96})$ ]] || fail 'runtime credential must be 32-byte or 48-byte lowercase hex'
[[ "$encryption_passphrase" =~ ^[0-9a-f]{96}$ ]] || fail 'state encryption passphrase must be 48-byte lowercase hex'

pg_host=${STATE_PG_HOST:-Admin.localdomain}
pg_port=${STATE_PG_PORT:-55432}
pg_root_cert=${STATE_PG_ROOT_CERT:-/etc/ssl/certs/ssl-cert-snakeoil.pem}
[[ "$pg_host" =~ ^[A-Za-z0-9.-]+$ ]] || fail 'invalid PostgreSQL host'
[[ "$pg_port" =~ ^[0-9]+$ ]] && ((pg_port >= 1 && pg_port <= 65535)) || fail 'invalid PostgreSQL port'
[[ "$pg_root_cert" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail 'invalid PostgreSQL root certificate path'
[ -f "$pg_root_cert" ] || fail 'PostgreSQL root certificate is missing'
pg_root_cert_native=$pg_root_cert
if is_windows_host; then pg_root_cert_native=$(cygpath -m "$pg_root_cert"); fi
root_cert_uri=${pg_root_cert_native//%/%25}
root_cert_uri=${root_cert_uri//:/%3A}
root_cert_uri=${root_cert_uri//\//%2F}

export PGPASSWORD="$runtime_credential"
export PG_CONN_STR="postgres://$STATE_ROLE@$pg_host:$pg_port/$STATE_DATABASE?sslmode=verify-full&sslrootcert=$root_cert_uri"
export PG_SCHEMA_NAME="$STATE_SCHEMA"
export PG_SKIP_SCHEMA_CREATION=${PG_SKIP_SCHEMA_CREATION:-true}
export PG_SKIP_TABLE_CREATION=${PG_SKIP_TABLE_CREATION:-true}
export PG_SKIP_INDEX_CREATION=${PG_SKIP_INDEX_CREATION:-true}
TF_ENCRYPTION=$(cat <<EOF_HCL
key_provider "pbkdf2" "liqi_state_v1" {
  passphrase               = "$encryption_passphrase"
  key_length               = 32
  iterations               = 600000
  salt_length              = 32
  hash_function            = "sha512"
  encrypted_metadata_alias = "liqi-state-v1"
}

method "aes_gcm" "liqi_state_v1" {
  keys = key_provider.pbkdf2.liqi_state_v1
}

state {
  method = method.aes_gcm.liqi_state_v1
}

plan {
  method = method.aes_gcm.liqi_state_v1
}
EOF_HCL
)
export TF_ENCRYPTION

# OpenTofu's pg backend accepts PG_CONN_STR and standard password input but
# fails closed on libpq service/passfile variables it does not implement.
# PostgreSQL administration scripts still receive those variables unchanged.
if [ "$(basename "$1")" = "tofu" ]; then
  unset PGSERVICEFILE PGPASSFILE
fi

exec "$@"
