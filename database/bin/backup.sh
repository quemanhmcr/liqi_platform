#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BACKUP_TYPE=${1:-}
case "$BACKUP_TYPE" in full|diff) ;; *) echo 'usage: backup.sh <full|diff>' >&2; exit 64 ;; esac

required=(LIQI_OCI_OBJECT_NAMESPACE LIQI_OCI_REGION LIQI_DATABASE_BACKUP_BUCKET LIQI_DATABASE_HOST_REF)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "required environment variable missing: $name" >&2; exit 64; }
done

PGBACKREST_COMMAND=${LIQI_PGBACKREST_COMMAND:-"$ROOT_DIR/database/bin/pgbackrest-command.sh"}
METADATA_DIR=${LIQI_BACKUP_METADATA_DIR:-/var/lib/liqi/postgresql/backup-staging/metadata}
LOCK_FILE=${LIQI_BACKUP_LOCK_FILE:-/run/lock/liqi-database-backup.lock}
OBJECT_PREFIX=${LIQI_BACKUP_METADATA_OBJECT_PREFIX:-postgresql/v0/metadata}
OCI=${OCI:-oci}

for command_name in psql python sha256sum flock "$OCI"; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "required command missing: $command_name" >&2; exit 69; }
done
[[ -x "$PGBACKREST_COMMAND" ]] || { echo "pgBackRest command boundary unavailable" >&2; exit 69; }

mkdir -p "$(dirname "$LOCK_FILE")" "$METADATA_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo 'another database backup is already running' >&2; exit 75; }

# Fail closed before creating the recovery probe or writing repository data.
"$ROOT_DIR/database/bin/backup-capacity-check.sh"

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
run_id=$(PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
import uuid
print(uuid.uuid4())
PY
)

"$ROOT_DIR/database/bin/create-recovery-probe.sh" > "$temporary/probe.json"
"$ROOT_DIR/database/bin/backup-verification-state.sh" > "$temporary/database-state.json"
manifest_sha=$(sha256sum "$ROOT_DIR/database/migrations/manifest.sha256" | awk '{print $1}')
migration_version=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["migrationVersion"])' "$temporary/database-state.json")
probe_event_id=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["probe"]["eventId"])' "$temporary/database-state.json")

"$PGBACKREST_COMMAND" --stanza=liqi check
"$PGBACKREST_COMMAND" \
  --stanza=liqi \
  --type="$BACKUP_TYPE" \
  --annotation="liqi-run-id=$run_id" \
  --annotation="liqi-migration-version=$migration_version" \
  --annotation="liqi-probe-event-id=$probe_event_id" \
  --annotation="liqi-manifest-sha256=$manifest_sha" \
  backup
"$PGBACKREST_COMMAND" --stanza=liqi --output=json info > "$temporary/pgbackrest-info.json"
pgbackrest_version=$("$PGBACKREST_COMMAND" version | awk '{print $NF}')

metadata="$METADATA_DIR/$run_id.json"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" create-metadata \
  --info "$temporary/pgbackrest-info.json" \
  --database-state "$temporary/database-state.json" \
  --manifest "$ROOT_DIR/database/migrations/manifest.sha256" \
  --run-id "$run_id" \
  --backup-type "$BACKUP_TYPE" \
  --bucket "$LIQI_DATABASE_BACKUP_BUCKET" \
  --namespace "$LIQI_OCI_OBJECT_NAMESPACE" \
  --region "$LIQI_OCI_REGION" \
  --host-ref "$LIQI_DATABASE_HOST_REF" \
  --pgbackrest-version "$pgbackrest_version" \
  --output "$metadata" >/dev/null

label=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["backup"]["label"])' "$metadata")
final_metadata="$METADATA_DIR/$label.json"
final_checksum="$final_metadata.sha256"
mv "$metadata" "$final_metadata"
checksum_value=$(sha256sum "$final_metadata" | awk '{print $1}')
printf '%s  %s\n' "$checksum_value" "$(basename "$final_metadata")" > "$final_checksum"
rm -f "$metadata.sha256"

checksum_object="$OBJECT_PREFIX/$label.json.sha256"
metadata_object="$OBJECT_PREFIX/$label.json"
# Publish metadata first and checksum last. The checksum is the completion marker;
# readers fetch it first and therefore never accept a partial publication.
"$OCI" os object put --auth instance_principal \
  --namespace-name "$LIQI_OCI_OBJECT_NAMESPACE" \
  --bucket-name "$LIQI_DATABASE_BACKUP_BUCKET" \
  --name "$metadata_object" \
  --file "$final_metadata" --no-overwrite --output json >/dev/null
"$OCI" os object put --auth instance_principal \
  --namespace-name "$LIQI_OCI_OBJECT_NAMESPACE" \
  --bucket-name "$LIQI_DATABASE_BACKUP_BUCKET" \
  --name "$checksum_object" \
  --file "$final_checksum" --no-overwrite --output json >/dev/null

# Update local latest evidence only after durable off-host publication completes.
latest_metadata="$METADATA_DIR/latest.json"
latest_checksum="$latest_metadata.sha256"
latest_metadata_tmp="$METADATA_DIR/.latest.$run_id.json"
latest_checksum_tmp="$latest_metadata_tmp.sha256"
cp "$final_metadata" "$latest_metadata_tmp"
latest_sha=$(sha256sum "$latest_metadata_tmp" | awk '{print $1}')
printf '%s  %s\n' "$latest_sha" "$(basename "$latest_metadata")" > "$latest_checksum_tmp"
mv -f "$latest_metadata_tmp" "$latest_metadata"
mv -f "$latest_checksum_tmp" "$latest_checksum"

"$ROOT_DIR/database/bin/backup-status.sh" --output "$METADATA_DIR/backup-status-v0.json" >/dev/null

PYTHONDONTWRITEBYTECODE=1 python - "$label" "$BACKUP_TYPE" "$metadata_object" "$checksum_object" <<'PY'
import json, sys
print(json.dumps({
    "backupLabel": sys.argv[1],
    "backupType": sys.argv[2],
    "metadataObject": sys.argv[3],
    "checksumObject": sys.argv[4],
    "durableMetadataPublished": True,
    "passed": True,
}, separators=(",", ":")))
PY
