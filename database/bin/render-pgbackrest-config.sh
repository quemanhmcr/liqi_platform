#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TEMPLATE=${LIQI_PGBACKREST_TEMPLATE:-"$ROOT_DIR/database/config/pgbackrest.conf.template"}
OUTPUT=${LIQI_PGBACKREST_CONFIG_PATH:-/etc/pgbackrest/pgbackrest.conf}
required=(LIQI_PGDATA LIQI_PGBACKREST_SPOOL_PATH LIQI_PGBACKREST_LOG_PATH LIQI_PGBACKREST_REPO_HOST LIQI_PGBACKREST_WRAPPER_PATH)
for name in "${required[@]}"; do [[ -n "${!name:-}" ]] || { echo "$name is required" >&2; exit 64; }; done
export LIQI_PGBACKREST_REPO_PORT=${LIQI_PGBACKREST_REPO_PORT:-8432}
export LIQI_PGBACKREST_REPO_PATH=${LIQI_PGBACKREST_REPO_PATH:-/independent-storage/pgbackrest/liqi}
[[ "$LIQI_PGBACKREST_REPO_HOST" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || { echo 'repository host must be a DNS name or reviewed overlay hostname' >&2; exit 64; }
[[ "$LIQI_PGBACKREST_REPO_PORT" == 8432 ]] || { echo 'repository port must remain 8432' >&2; exit 64; }
[[ "$LIQI_PGBACKREST_REPO_PATH" == /independent-storage/pgbackrest/liqi ]] || { echo 'repository path must remain the approved independent path' >&2; exit 64; }
python - "$TEMPLATE" "$OUTPUT" <<'PY'
import os,sys,tempfile
from pathlib import Path
template,output=map(Path,sys.argv[1:])
if not template.is_file(): raise SystemExit('pgBackRest template is unavailable')
for name in ('LIQI_PGDATA','LIQI_PGBACKREST_SPOOL_PATH','LIQI_PGBACKREST_LOG_PATH','LIQI_PGBACKREST_REPO_PATH','LIQI_PGBACKREST_WRAPPER_PATH'):
 value=os.environ[name]
 if not value.startswith('/') or '..' in Path(value).parts: raise SystemExit(f'{name} must be an absolute bounded path')
replace={
 '@@PGDATA@@':os.environ['LIQI_PGDATA'],'@@SPOOL_PATH@@':os.environ['LIQI_PGBACKREST_SPOOL_PATH'],'@@LOG_PATH@@':os.environ['LIQI_PGBACKREST_LOG_PATH'],
 '@@REPO_HOST@@':os.environ['LIQI_PGBACKREST_REPO_HOST'],'@@REPO_PORT@@':os.environ['LIQI_PGBACKREST_REPO_PORT'],'@@REPO_PATH@@':os.environ['LIQI_PGBACKREST_REPO_PATH'],'@@PGBACKREST_WRAPPER@@':os.environ['LIQI_PGBACKREST_WRAPPER_PATH']}
text=template.read_text(encoding='utf-8')
for token,value in replace.items(): text=text.replace(token,value)
if '@@' in text: raise SystemExit('unresolved pgBackRest template token')
output.parent.mkdir(parents=True,exist_ok=True)
fd,tmp=tempfile.mkstemp(prefix='.'+output.name+'.',dir=output.parent,text=True); os.close(fd)
Path(tmp).write_text(text,encoding='utf-8',newline='\n'); os.chmod(tmp,0o640); os.replace(tmp,output)
PY
