#!/usr/bin/env bash
set -euo pipefail
umask 077

TARGET_ROOT=${1:-}
TARGET_DATABASE=${2:-}
MARKER="$TARGET_ROOT/exercise.json"
[[ "$TARGET_ROOT" =~ ^/var/lib/liqi/recovery-exercises/[A-Za-z0-9._/-]+$ ]] || { echo 'unsafe recovery target root' >&2; exit 64; }
BASE_ROOT=/var/lib/liqi/recovery-exercises
canonical_target=$(python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$TARGET_ROOT")
[[ "$canonical_target" == "$TARGET_ROOT" && "$canonical_target" == "$BASE_ROOT"/* ]] || { echo 'unsafe recovery target root' >&2; exit 64; }
[[ "$TARGET_DATABASE" =~ ^liqi_restore_[a-z0-9_]{3,48}$ ]] || { echo 'target database name is invalid' >&2; exit 64; }
[[ ! -L "$TARGET_ROOT" ]] || { echo 'recovery target root must not be a symlink' >&2; exit 64; }
[[ -r "$MARKER" ]] || { echo 'recovery target marker is missing; refusing cleanup' >&2; exit 66; }
python - "$MARKER" "$TARGET_ROOT" "$TARGET_DATABASE" <<'PY'
import json,sys
from pathlib import Path
marker,root,database=sys.argv[1:]
payload=json.loads(Path(marker).read_text(encoding='utf-8'))
if payload.get('schema_version')!='database-recovery-exercise-target-v0' or payload.get('target_root')!=root or payload.get('target_database')!=database or payload.get('isolated') is not True:
    raise SystemExit('recovery target marker does not authorize cleanup')
PY

pgdata="$TARGET_ROOT/restore/data"
if [[ -f "$pgdata/postmaster.pid" ]]; then
  PG_CTL=${PG_CTL:-pg_ctl}
  command -v "$PG_CTL" >/dev/null 2>&1 || { echo 'pg_ctl required to stop restore target before cleanup' >&2; exit 69; }
  "$PG_CTL" -D "$pgdata" -m immediate -w stop
fi
rm -rf --one-file-system -- "$TARGET_ROOT"
printf '%s\n' "cleaned isolated recovery target: $TARGET_ROOT"
