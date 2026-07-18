#!/usr/bin/env bash
set -euo pipefail
umask 077
TEMPLATE=${1:-$(dirname "$0")/pgbackrest-server.conf.template}; OUTPUT=${2:-/etc/pgbackrest/liqi-repository.conf}
: "${LIQI_MANAGEMENT_WIREGUARD_ADDRESS:?LIQI_MANAGEMENT_WIREGUARD_ADDRESS is required}"
: "${LIQI_PGBACKREST_CLIENT_CN:?LIQI_PGBACKREST_CLIENT_CN is required}"
: "${LIQI_PGBACKREST_SERVER_CA_FILE:?LIQI_PGBACKREST_SERVER_CA_FILE is required}"
: "${LIQI_PGBACKREST_SERVER_CERT_FILE:?LIQI_PGBACKREST_SERVER_CERT_FILE is required}"
: "${LIQI_PGBACKREST_SERVER_KEY_FILE:?LIQI_PGBACKREST_SERVER_KEY_FILE is required}"
[[ "$LIQI_MANAGEMENT_WIREGUARD_ADDRESS" =~ ^10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || { echo 'server address must be a reviewed private WireGuard IPv4 address' >&2; exit 64; }
[[ "$LIQI_PGBACKREST_CLIENT_CN" =~ ^[A-Za-z0-9._-]{3,64}$ ]] || { echo 'client certificate CN is invalid' >&2; exit 64; }
for p in "$LIQI_PGBACKREST_SERVER_CA_FILE" "$LIQI_PGBACKREST_SERVER_CERT_FILE" "$LIQI_PGBACKREST_SERVER_KEY_FILE"; do [[ "$p" == /* && "$p" != *'..'* ]] || { echo 'TLS paths must be absolute and bounded' >&2; exit 64; }; done
python - "$TEMPLATE" "$OUTPUT" <<'PY'
import os,sys,tempfile
from pathlib import Path
template,output=map(Path,sys.argv[1:]); text=template.read_text(encoding='utf-8')
values={'@@WIREGUARD_ADDRESS@@':os.environ['LIQI_MANAGEMENT_WIREGUARD_ADDRESS'],'@@CLIENT_CN@@':os.environ['LIQI_PGBACKREST_CLIENT_CN'],'@@TLS_CA_FILE@@':os.environ['LIQI_PGBACKREST_SERVER_CA_FILE'],'@@TLS_CERT_FILE@@':os.environ['LIQI_PGBACKREST_SERVER_CERT_FILE'],'@@TLS_KEY_FILE@@':os.environ['LIQI_PGBACKREST_SERVER_KEY_FILE']}
for token,value in values.items(): text=text.replace(token,value)
if '@@' in text: raise SystemExit('unresolved server configuration token')
output.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix='.'+output.name+'.',dir=output.parent,text=True); os.close(fd); Path(tmp).write_text(text,encoding='utf-8',newline='\n'); os.chmod(tmp,0o600); os.replace(tmp,output)
PY
