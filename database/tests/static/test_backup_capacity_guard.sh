#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
cat > "$temporary/oci" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${FAKE_BUCKET_SIZE:-0}"
FAKE
cat > "$temporary/psql" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${FAKE_DATABASE_SIZE:-0}"
FAKE
chmod +x "$temporary/oci" "$temporary/psql"

common=(
  LIQI_OCI_OBJECT_NAMESPACE=example-namespace
  LIQI_DATABASE_BACKUP_BUCKET=liqi-database-backup-v0
  OCI="$temporary/oci"
  PSQL="$temporary/psql"
)

env "${common[@]}" FAKE_BUCKET_SIZE=1073741824 FAKE_DATABASE_SIZE=1073741824 \
  "$ROOT_DIR/database/bin/backup-capacity-check.sh" > "$temporary/allowed.json"
PYTHONDONTWRITEBYTECODE=1 python - "$temporary/allowed.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["allowed"] is True and value["reason"] == "within-v0-capacity"
PY

set +e
env "${common[@]}" FAKE_BUCKET_SIZE=17179869184 FAKE_DATABASE_SIZE=2147483648 \
  "$ROOT_DIR/database/bin/backup-capacity-check.sh" > "$temporary/blocked.json"
status=$?
set -e
[[ "$status" -eq 75 ]]
PYTHONDONTWRITEBYTECODE=1 python - "$temporary/blocked.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["allowed"] is False and value["reason"] == "object-storage-peak-cap-exceeded"
PY

set +e
env "${common[@]}" FAKE_BUCKET_SIZE=null FAKE_DATABASE_SIZE=1 \
  "$ROOT_DIR/database/bin/backup-capacity-check.sh" > "$temporary/unknown.json"
status=$?
set -e
[[ "$status" -eq 75 ]]
PYTHONDONTWRITEBYTECODE=1 python - "$temporary/unknown.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["allowed"] is False and value["reason"] == "object-storage-usage-unknown"
PY

printf '%s\n' '{"validation":"database-backup-capacity-v0","passed":true}'
