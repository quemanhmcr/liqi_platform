#!/usr/bin/env bash
set -euo pipefail
: "${LIQI_OCI_OBJECT_NAMESPACE:?LIQI_OCI_OBJECT_NAMESPACE is required}"
: "${LIQI_DATABASE_BACKUP_BUCKET:?LIQI_DATABASE_BACKUP_BUCKET is required}"

OCI=${OCI:-oci}
PSQL=${PSQL:-psql}
OBJECT_CAP_BYTES=${LIQI_DATABASE_BACKUP_OBJECT_CAP_BYTES:-19327352832} # 18 GiB
DATABASE_CAP_BYTES=${LIQI_DATABASE_DATA_CAP_BYTES:-8589934592}         # 8 GiB
SAFETY_MARGIN_BYTES=${LIQI_DATABASE_BACKUP_SAFETY_MARGIN_BYTES:-1073741824} # 1 GiB

for command_name in "$OCI" "$PSQL"; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "required command missing: $command_name" >&2; exit 69; }
done
for value_name in OBJECT_CAP_BYTES DATABASE_CAP_BYTES SAFETY_MARGIN_BYTES; do
  value=${!value_name}
  [[ "$value" =~ ^[0-9]+$ ]] || { echo "$value_name must be an integer byte count" >&2; exit 64; }
done

bucket_size=$("$OCI" os bucket get --auth instance_principal \
  --namespace-name "$LIQI_OCI_OBJECT_NAMESPACE" \
  --bucket-name "$LIQI_DATABASE_BACKUP_BUCKET" \
  --fields approximateSize \
  --query 'data."approximate-size"' \
  --raw-output)
if [[ ! "$bucket_size" =~ ^[0-9]+$ ]]; then
  PYTHONDONTWRITEBYTECODE=1 python - "$bucket_size" <<'PY'
import json, sys
print(json.dumps({
    "schemaVersion": "database-backup-capacity-v0",
    "allowed": False,
    "reason": "object-storage-usage-unknown",
    "actual": sys.argv[1],
    "operatorAction": "Do not run backup until OCI reports approximateSize for the dedicated bucket.",
}, separators=(",", ":")))
PY
  exit 75
fi

database_size=$("$PSQL" --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-/run/postgresql}" --port="${PGPORT:-5432}" \
  --username="${PGUSER:-postgres}" --dbname="${PGDATABASE:-liqi}" \
  -c 'SELECT pg_database_size(current_database())::bigint')
if [[ ! "$database_size" =~ ^[0-9]+$ ]]; then
  echo 'PostgreSQL did not return a numeric database size' >&2
  exit 70
fi

projected_peak=$((bucket_size + database_size + SAFETY_MARGIN_BYTES))
reason=within-v0-capacity
allowed=true
if (( database_size > DATABASE_CAP_BYTES )); then
  allowed=false
  reason=database-data-cap-exceeded
elif (( projected_peak > OBJECT_CAP_BYTES )); then
  allowed=false
  reason=object-storage-peak-cap-exceeded
fi

PYTHONDONTWRITEBYTECODE=1 python - \
  "$allowed" "$reason" "$bucket_size" "$database_size" "$SAFETY_MARGIN_BYTES" \
  "$projected_peak" "$OBJECT_CAP_BYTES" "$DATABASE_CAP_BYTES" <<'PY'
import json, sys
allowed = sys.argv[1] == "true"
print(json.dumps({
    "schemaVersion": "database-backup-capacity-v0",
    "allowed": allowed,
    "reason": sys.argv[2],
    "bucketApproximateSizeBytes": int(sys.argv[3]),
    "databaseSizeBytes": int(sys.argv[4]),
    "safetyMarginBytes": int(sys.argv[5]),
    "projectedPeakBytes": int(sys.argv[6]),
    "objectCapBytes": int(sys.argv[7]),
    "databaseCapBytes": int(sys.argv[8]),
    "operatorAction": None if allowed else "Approve PAYG/capacity expansion or safely expire verified retention before retrying.",
}, separators=(",", ":")))
PY

[[ "$allowed" == true ]] || exit 75
