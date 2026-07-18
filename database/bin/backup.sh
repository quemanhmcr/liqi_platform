#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BACKUP_TYPE=${1:-}
case "$BACKUP_TYPE" in full|diff) ;; *) echo 'usage: backup.sh <full|diff>' >&2; exit 64 ;; esac
required=(LIQI_DATABASE_HOST_REF LIQI_SOURCE_GIT_SHA)
for name in "${required[@]}"; do [[ -n "${!name:-}" ]] || { echo "required environment variable missing: $name" >&2; exit 64; }; done
[[ "$LIQI_SOURCE_GIT_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'LIQI_SOURCE_GIT_SHA must be an exact lowercase Git SHA' >&2; exit 64; }
[[ "$LIQI_DATABASE_HOST_REF" =~ ^oci-live-v1://host/[a-z0-9][a-z0-9-]{2,63}$ ]] || { echo 'LIQI_DATABASE_HOST_REF is invalid' >&2; exit 64; }
PGBACKREST_COMMAND=${LIQI_PGBACKREST_COMMAND:-"$ROOT_DIR/database/bin/pgbackrest-command.sh"}
METADATA_DIR=${LIQI_BACKUP_METADATA_DIR:-/var/lib/liqi/postgresql/backup-staging/metadata}
LOCK_FILE=${LIQI_BACKUP_LOCK_FILE:-/run/lock/liqi-database-backup.lock}
for command_name in psql python sha256sum flock; do command -v "$command_name" >/dev/null 2>&1 || { echo "required command missing: $command_name" >&2; exit 69; }; done
[[ -x "$PGBACKREST_COMMAND" ]] || { echo 'pgBackRest command boundary unavailable' >&2; exit 69; }
mkdir -p "$(dirname "$LOCK_FILE")" "$METADATA_DIR"; chmod 700 "$METADATA_DIR"
exec 9>"$LOCK_FILE"; flock -n 9 || { echo 'another database backup is already running' >&2; exit 75; }
"$ROOT_DIR/database/bin/backup-capacity-check.sh"
temporary=$(mktemp -d); trap 'rm -rf "$temporary"' EXIT
run_id=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import uuid; print(uuid.uuid4())')
"$ROOT_DIR/database/bin/create-recovery-probe.sh" > "$temporary/probe.json"
"$ROOT_DIR/database/bin/backup-verification-state.sh" > "$temporary/database-state.json"
mapfile -t values < <(PYTHONDONTWRITEBYTECODE=1 python - "$temporary/database-state.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1],encoding='utf-8')); p=s['probe']
for value in (s['postgresqlVersion'],s['migrationVersion'],p['probeId'],p['eventId'],p['completedAt'],p['probeStatus'],p['outboxState'],p['effectCount']): print(value)
PY
)
[[ ${#values[@]} -eq 8 ]] || { echo 'database recovery state is incomplete' >&2; exit 65; }
postgresql_version=${values[0]}; migration_version=${values[1]}; probe_id=${values[2]}; probe_event_id=${values[3]}; probe_completed_at=${values[4]}; probe_status=${values[5]}; outbox_state=${values[6]}; effect_count=${values[7]}
manifest_sha=$(sha256sum "$ROOT_DIR/database/migrations/manifest.sha256" | awk '{print $1}')
"$PGBACKREST_COMMAND" --stanza=liqi check
"$PGBACKREST_COMMAND" --stanza=liqi --type="$BACKUP_TYPE" \
  --annotation="liqi-run-id=$run_id" \
  --annotation="liqi-source-git-sha=$LIQI_SOURCE_GIT_SHA" \
  --annotation="liqi-host-ref=$LIQI_DATABASE_HOST_REF" \
  --annotation="liqi-postgresql-version=$postgresql_version" \
  --annotation="liqi-migration-version=$migration_version" \
  --annotation="liqi-probe-id=$probe_id" \
  --annotation="liqi-probe-event-id=$probe_event_id" \
  --annotation="liqi-probe-completed-at=$probe_completed_at" \
  --annotation="liqi-probe-status=$probe_status" \
  --annotation="liqi-outbox-state=$outbox_state" \
  --annotation="liqi-effect-count=$effect_count" \
  --annotation="liqi-manifest-sha256=$manifest_sha" \
  --annotation="liqi-repository-ref=management://database-backup-repository" \
  --annotation="liqi-repository-path=/independent-storage/pgbackrest/liqi" \
  --annotation="liqi-repository-port=8432" backup
"$PGBACKREST_COMMAND" --stanza=liqi --output=json info > "$temporary/pgbackrest-info.json"
metadata="$METADATA_DIR/$run_id.json"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" create-metadata \
  --info "$temporary/pgbackrest-info.json" --database-state "$temporary/database-state.json" \
  --manifest "$ROOT_DIR/database/migrations/manifest.sha256" --run-id "$run_id" --backup-type "$BACKUP_TYPE" \
  --host-ref "$LIQI_DATABASE_HOST_REF" --source-git-sha "$LIQI_SOURCE_GIT_SHA" --output "$metadata" >/dev/null
label=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["backup"]["label"])' "$metadata")
final_metadata="$METADATA_DIR/$label.json"; final_checksum="$final_metadata.sha256"
[[ ! -e "$final_metadata" && ! -e "$final_checksum" ]] || { echo 'backup label metadata already exists locally' >&2; exit 65; }
mv "$metadata" "$final_metadata"; mv "$metadata.sha256" "$final_checksum"
checksum_value=$(sha256sum "$final_metadata" | awk '{print $1}'); printf '%s  %s\n' "$checksum_value" "$(basename "$final_metadata")" > "$final_checksum"
latest_metadata="$METADATA_DIR/latest.json"; latest_checksum="$latest_metadata.sha256"; latest_metadata_tmp="$METADATA_DIR/.latest.$run_id.json"; latest_checksum_tmp="$latest_metadata_tmp.sha256"
cp "$final_metadata" "$latest_metadata_tmp"; printf '%s  %s\n' "$checksum_value" "$(basename "$latest_metadata")" > "$latest_checksum_tmp"; mv -f "$latest_metadata_tmp" "$latest_metadata"; mv -f "$latest_checksum_tmp" "$latest_checksum"
"$ROOT_DIR/database/bin/backup-status.sh" --output "$METADATA_DIR/backup-status-v0.json" >/dev/null
PYTHONDONTWRITEBYTECODE=1 python - "$label" "$BACKUP_TYPE" "$final_metadata" <<'PY'
import json,sys
print(json.dumps({'backupLabel':sys.argv[1],'backupType':sys.argv[2],'metadataEvidence':sys.argv[3],'durableAuthority':'pgbackrest-backup-annotations','repositoryRef':f'pgbackrest://management/database-backup-repository/liqi/{sys.argv[1]}','passed':True},separators=(',',':')))
PY
