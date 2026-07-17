#!/usr/bin/env bash
set -euo pipefail
umask 077

TARGET_ROOT=${1:-}
TARGET_DATABASE=${2:-}
BASE_ROOT=/var/lib/liqi/recovery-exercises

[[ "$TARGET_ROOT" =~ ^/var/lib/liqi/recovery-exercises/[A-Za-z0-9._/-]+$ ]] || {
  echo "target root must be a bounded path below $BASE_ROOT" >&2
  exit 64
}
[[ "$TARGET_DATABASE" =~ ^liqi_restore_[a-z0-9_]{3,48}$ ]] || {
  echo 'target database name is invalid' >&2
  exit 64
}
canonical_target=$(python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$TARGET_ROOT")
[[ "$canonical_target" == "$TARGET_ROOT" && "$canonical_target" == "$BASE_ROOT"/* ]] || {
  echo "target root must resolve directly below $BASE_ROOT" >&2
  exit 64
}
[[ ! -L "$TARGET_ROOT" ]] || { echo 'target root must not be a symlink' >&2; exit 64; }
if [[ -e "$TARGET_ROOT" ]] && find "$TARGET_ROOT" -mindepth 1 -print -quit | grep -q .; then
  echo 'target root already exists and is not empty' >&2
  exit 65
fi

mkdir -p "$TARGET_ROOT"/{metadata,restore,run,evidence}
chmod 700 "$TARGET_ROOT" "$TARGET_ROOT"/{metadata,restore,run,evidence}
marker="$TARGET_ROOT/exercise.json"
tmp="$TARGET_ROOT/.exercise.$$.json"
python - "$TARGET_ROOT" "$TARGET_DATABASE" "$tmp" <<'PY'
import json,sys
from pathlib import Path
root,database,output=sys.argv[1:]
Path(output).write_text(json.dumps({
    'schema_version':'database-recovery-exercise-target-v0',
    'target_root':root,
    'target_database':database,
    'isolated':True,
    'production_traffic':False,
},indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
PY
mv -f "$tmp" "$marker"
printf '%s\n' "prepared isolated recovery target: $TARGET_ROOT"
