#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BACKUP_REF=${1:-}; TARGET_ROOT=${2:-}; TARGET_DATABASE=${3:-}; MARKER="$TARGET_ROOT/exercise.json"; BASE_ROOT=/var/lib/liqi/recovery-exercises
canonical_target=$(python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$TARGET_ROOT")
[[ "$canonical_target" == "$TARGET_ROOT" && "$canonical_target" == "$BASE_ROOT"/* ]] || { echo 'unsafe recovery target root' >&2; exit 64; }
[[ "$TARGET_DATABASE" =~ ^liqi_restore_[a-z0-9_]{3,48}$ ]] || { echo 'target database name is invalid' >&2; exit 64; }
prefix=pgbackrest://management/database-backup-repository/liqi/
[[ "$BACKUP_REF" == "$prefix"* ]] || { echo 'backup_ref must identify the independent pgBackRest repository' >&2; exit 64; }
label=${BACKUP_REF#"$prefix"}
[[ "$label" =~ ^[0-9]{8}-[0-9]{6}[FDI](?:_[0-9]{8}-[0-9]{6}[DI])?$ ]] || { echo 'backup_ref label is invalid' >&2; exit 64; }
[[ -r "$MARKER" ]] || { echo 'prepared recovery target marker is missing' >&2; exit 66; }
python - "$MARKER" "$TARGET_ROOT" "$TARGET_DATABASE" <<'PY'
import json,sys
from pathlib import Path
marker,root,database=sys.argv[1:]; payload=json.loads(Path(marker).read_text(encoding='utf-8'))
if payload.get('schema_version')!='database-recovery-exercise-target-v0' or payload.get('target_root')!=root or payload.get('target_database')!=database or payload.get('isolated') is not True: raise SystemExit('prepared recovery target marker does not match requested target')
PY
metadata_dir="$TARGET_ROOT/metadata"
"$ROOT_DIR/database/recovery/fetch-backup-metadata.sh" "$label" "$metadata_dir"
metadata="$metadata_dir/$label.json"; checksum="$metadata.sha256"; result="$TARGET_ROOT/evidence/restore-result.json"; source_metadata_dir="$TARGET_ROOT/evidence/restore-source"
mkdir -p "$source_metadata_dir"; chmod 700 "$source_metadata_dir"
LIQI_RESTORE_METADATA_FILE="$metadata" LIQI_RESTORE_METADATA_CHECKSUM_FILE="$checksum" LIQI_RESTORE_ROOT="$TARGET_ROOT/restore" LIQI_RESTORE_TARGET_PGDATA="$TARGET_ROOT/restore/data" LIQI_RESTORE_SOCKET_DIR="$TARGET_ROOT/run" LIQI_RESTORE_RESULT_FILE="$result" LIQI_BACKUP_METADATA_DIR="$source_metadata_dir" LIQI_RESTORE_LATEST_EVIDENCE_DIR="$TARGET_ROOT/evidence/latest" LIQI_KEEP_RESTORE_RUNNING="${LIQI_KEEP_RESTORE_RUNNING:-false}" "$ROOT_DIR/database/recovery/restore.sh"
source_metadata="$source_metadata_dir/$label.json"; state="$TARGET_ROOT/recovery-state.json"; tmp="$TARGET_ROOT/.recovery-state.$$.json"
python - "$BACKUP_REF" "$TARGET_ROOT" "$TARGET_DATABASE" "$metadata" "$result" "$source_metadata" "$tmp" <<'PY'
import json,sys
from pathlib import Path
backup_ref,target_root,target_database,metadata,result,source_metadata,output=sys.argv[1:]
Path(output).write_text(json.dumps({'schema_version':'database-recovery-exercise-state-v0','backup_ref':backup_ref,'target_root':target_root,'target_database':target_database,'metadata':metadata,'restore_result':result,'restore_source_metadata':source_metadata},indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
PY
mv -f "$tmp" "$state"; printf '%s\n' "restored and provider-verified backup $label into isolated target"
