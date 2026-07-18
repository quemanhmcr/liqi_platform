#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d); trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/secrets" "$temporary/config"
printf '%s\n' 'test-ca' > "$temporary/secrets/ca"
printf '%s\n' 'test-client-cert' > "$temporary/secrets/cert"
printf '%s\n' 'test-client-private-key-material' > "$temporary/secrets/key"
printf '%s\n' 'test-cipher-passphrase-at-least-24' > "$temporary/secrets/cipher"
chmod 600 "$temporary/secrets"/*
cat > "$temporary/fake-pgbackrest" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
[[ "$PGBACKREST_REPO1_HOST_CA_FILE" == */secrets/ca ]]
[[ "$PGBACKREST_REPO1_HOST_CERT_FILE" == */secrets/cert ]]
[[ "$PGBACKREST_REPO1_HOST_KEY_FILE" == */secrets/key ]]
[[ "$PGBACKREST_REPO1_CIPHER_PASS" == 'test-cipher-passphrase-at-least-24' ]]
case " $* " in *test-client-private-key-material*|*test-cipher-passphrase-at-least-24*) exit 9;; esac
printf '%s\n' 'fake pgBackRest TLS boundary passed'
FAKE
chmod +x "$temporary/fake-pgbackrest"
printf '%s\n' '[global]' > "$temporary/config/pgbackrest.conf"
PGBACKREST_BIN="$temporary/fake-pgbackrest" LIQI_PGBACKREST_CONFIG_PATH="$temporary/config/pgbackrest.conf" LIQI_SECRET_PGBACKREST_REPO_CA_FILE="$temporary/secrets/ca" LIQI_SECRET_PGBACKREST_REPO_CLIENT_CERT_FILE="$temporary/secrets/cert" LIQI_SECRET_PGBACKREST_REPO_CLIENT_KEY_FILE="$temporary/secrets/key" LIQI_SECRET_PGBACKREST_CIPHER_FILE="$temporary/secrets/cipher" "$ROOT_DIR/database/bin/pgbackrest-command.sh" version >/dev/null
MSYS2_ENV_CONV_EXCL='LIQI_PGDATA;LIQI_PGBACKREST_SPOOL_PATH;LIQI_PGBACKREST_LOG_PATH;LIQI_PGBACKREST_REPO_PATH;LIQI_PGBACKREST_WRAPPER_PATH' LIQI_PGDATA=/var/lib/liqi/postgresql/data LIQI_PGBACKREST_SPOOL_PATH=/var/spool/pgbackrest LIQI_PGBACKREST_LOG_PATH=/var/log/pgbackrest LIQI_PGBACKREST_REPO_HOST=management-repository.liqi.internal LIQI_PGBACKREST_REPO_PORT=8432 LIQI_PGBACKREST_REPO_PATH=/independent-storage/pgbackrest/liqi LIQI_PGBACKREST_WRAPPER_PATH=/usr/local/lib/liqi-database/database/bin/pgbackrest-command.sh LIQI_PGBACKREST_CONFIG_PATH="$temporary/config/rendered.conf" "$ROOT_DIR/database/bin/render-pgbackrest-config.sh" >/dev/null
grep -q 'repo1-host=management-repository.liqi.internal' "$temporary/config/rendered.conf"
grep -q 'repo1-host-type=tls' "$temporary/config/rendered.conf"
grep -q 'repo1-host-port=8432' "$temporary/config/rendered.conf"
grep -q 'repo1-cipher-type=aes-256-cbc' "$temporary/config/rendered.conf"
grep -q 'archive-push-queue-max=2GiB' "$temporary/config/rendered.conf"
grep -q 'cmd=/usr/local/lib/liqi-database/database/bin/pgbackrest-command.sh' "$temporary/config/rendered.conf"
if grep -Eqi 'test-client-private-key-material|test-cipher-passphrase|repo1-s3|repo1-cipher-pass=' "$temporary/config/rendered.conf"; then echo 'rendered configuration leaked secret or S3 material' >&2; exit 1; fi
printf '%s\n' '{"validation":"pgbackrest-tls-secret-boundary-v1","passed":true}'
