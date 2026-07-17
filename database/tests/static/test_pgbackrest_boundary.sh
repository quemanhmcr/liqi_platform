#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/secrets" "$temporary/config" "$temporary/spool" "$temporary/log" "$temporary/data"
printf '%s\n' 'test-access-key' > "$temporary/secrets/key"
printf '%s\n' 'test-secret-key' > "$temporary/secrets/secret"
printf '%s\n' 'test-cipher-passphrase' > "$temporary/secrets/cipher"
chmod 600 "$temporary/secrets"/*
cat > "$temporary/fake-pgbackrest" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[[ "$PGBACKREST_REPO1_S3_KEY" == 'test-access-key' ]]
[[ "$PGBACKREST_REPO1_S3_KEY_SECRET" == 'test-secret-key' ]]
[[ "$PGBACKREST_REPO1_CIPHER_PASS" == 'test-cipher-passphrase' ]]
case " $* " in
  *test-access-key*|*test-secret-key*|*test-cipher-passphrase*) exit 9 ;;
esac
printf '%s\n' 'fake pgBackRest boundary passed'
FAKE
chmod +x "$temporary/fake-pgbackrest"
printf '%s\n' '[global]' > "$temporary/config/pgbackrest.conf"

PGBACKREST_BIN="$temporary/fake-pgbackrest" \
LIQI_PGBACKREST_CONFIG_PATH="$temporary/config/pgbackrest.conf" \
LIQI_SECRET_PGBACKREST_S3_KEY_FILE="$temporary/secrets/key" \
LIQI_SECRET_PGBACKREST_S3_SECRET_FILE="$temporary/secrets/secret" \
LIQI_SECRET_PGBACKREST_CIPHER_FILE="$temporary/secrets/cipher" \
  "$ROOT_DIR/database/bin/pgbackrest-command.sh" version >/dev/null

MSYS2_ENV_CONV_EXCL='LIQI_PGDATA;LIQI_PGBACKREST_SPOOL_PATH;LIQI_PGBACKREST_LOG_PATH;LIQI_PGBACKREST_WRAPPER_PATH;LIQI_PGBACKREST_REPO_PATH' \
LIQI_PGDATA=/var/lib/liqi/postgresql/data \
LIQI_PGBACKREST_SPOOL_PATH=/var/spool/pgbackrest \
LIQI_PGBACKREST_LOG_PATH=/var/log/pgbackrest \
LIQI_OCI_OBJECT_NAMESPACE=example-namespace \
LIQI_OCI_REGION=ap-singapore-2 \
LIQI_DATABASE_BACKUP_BUCKET=liqi-database-backup-v0 \
LIQI_PGBACKREST_CONFIG_PATH="$temporary/config/rendered.conf" \
LIQI_PGBACKREST_WRAPPER_PATH=/opt/liqi/current/database/bin/pgbackrest-command.sh \
  "$ROOT_DIR/database/bin/render-pgbackrest-config.sh" >/dev/null

grep -q 'repo1-s3-uri-style=path' "$temporary/config/rendered.conf"
grep -q 'repo1-cipher-type=aes-256-cbc' "$temporary/config/rendered.conf"
grep -q 'archive-push-queue-max=2GiB' "$temporary/config/rendered.conf"
if grep -Eq 'test-access-key|test-secret-key|test-cipher-passphrase|repo1-s3-key=|repo1-s3-key-secret=|repo1-cipher-pass=' "$temporary/config/rendered.conf"; then
  echo 'rendered configuration leaked secret material' >&2
  exit 1
fi
printf '%s\n' '{"validation":"pgbackrest-secret-boundary-v0","passed":true}'
