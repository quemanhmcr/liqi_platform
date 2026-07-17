#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

TARGET_ROOT=${1:-}
TARGET_DATABASE=${2:-}
REQUIRED_MIGRATION=${3:-}
OUTPUT=${4:-}
ENVIRONMENT=${5:-}
STATE="$TARGET_ROOT/recovery-state.json"
BASE_ROOT=/var/lib/liqi/recovery-exercises
canonical_target=$(python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$TARGET_ROOT")
[[ "$canonical_target" == "$TARGET_ROOT" && "$canonical_target" == "$BASE_ROOT"/* ]] || { echo 'unsafe recovery target root' >&2; exit 64; }
[[ "$TARGET_DATABASE" =~ ^liqi_restore_[a-z0-9_]{3,48}$ ]] || { echo 'target database name is invalid' >&2; exit 64; }
BACKUP_STATUS=${LIQI_BACKUP_STATUS_FILE:-/var/lib/liqi/postgresql/backup-staging/metadata/backup-status-v0.json}

[[ "$REQUIRED_MIGRATION" =~ ^[0-9]+$ ]] || { echo 'required migration must be numeric' >&2; exit 64; }
[[ -n "$OUTPUT" ]] || { echo 'verification output is required' >&2; exit 64; }
case "$ENVIRONMENT" in development|staging|production) ;; *) echo 'source environment is invalid' >&2; exit 64 ;; esac
[[ -r "$STATE" ]] || { echo 'recovery state is unavailable' >&2; exit 66; }

mapfile -t values < <(python - "$STATE" "$TARGET_ROOT" "$TARGET_DATABASE" "$REQUIRED_MIGRATION" <<'PY'
import json,sys
from pathlib import Path
state_path,target_root,target_database,required=sys.argv[1:]
state=json.loads(Path(state_path).read_text(encoding='utf-8'))
if state.get('schema_version')!='database-recovery-exercise-state-v0' or state.get('target_root')!=target_root or state.get('target_database')!=target_database:
    raise SystemExit('recovery state does not match requested target')
metadata=Path(state['metadata'])
doc=json.loads(metadata.read_text(encoding='utf-8'))
if str(doc.get('migration',{}).get('currentVersion'))!=required:
    raise SystemExit(f'backup migration version does not satisfy required version {required}')
for key in ('metadata','restore_result','restore_source_metadata'):
    value=Path(state[key])
    if not value.is_file() or not Path(str(value)+'.sha256').is_file():
        raise SystemExit(f'required checksummed recovery evidence missing: {value}')
    print(value)
PY
)
[[ ${#values[@]} -eq 3 ]] || { echo 'recovery state did not resolve required evidence' >&2; exit 65; }
metadata=${values[0]}
restore_result=${values[1]}
restore_source=${values[2]}

LIQI_BACKUP_METADATA_CHECKSUM_FILE="$metadata.sha256" \
LIQI_RESTORE_RESULT_CHECKSUM_FILE="$restore_result.sha256" \
LIQI_BACKUP_STATUS_CHECKSUM_FILE="$BACKUP_STATUS.sha256" \
LIQI_RESTORE_SOURCE_METADATA_CHECKSUM_FILE="$restore_source.sha256" \
  "$ROOT_DIR/database/bin/recovery-status.sh" \
    --environment "$ENVIRONMENT" \
    --metadata "$metadata" \
    --restore-result "$restore_result" \
    --backup-status "$BACKUP_STATUS" \
    --restore-source-metadata "$restore_source" \
    --output "$OUTPUT"
