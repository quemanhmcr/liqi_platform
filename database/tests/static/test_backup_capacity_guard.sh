#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d); trap 'rm -rf "$temporary"' EXIT
cat > "$temporary/psql" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "${FAKE_DATABASE_SIZE:-0}"
FAKE
chmod +x "$temporary/psql"
source_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
write_capacity(){
  local output=$1 available=$2 reserved=$3 observed=${4:-now}
  PYTHONDONTWRITEBYTECODE=1 python - "$output" "$source_sha" "$available" "$reserved" "$observed" <<'PY'
import hashlib,json,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
output,sha,available,reserved,observed=sys.argv[1:]
when=datetime.now(timezone.utc) if observed=='now' else datetime.now(timezone.utc)-timedelta(hours=1)
doc={'schema_version':'liqi.database.backup-repository-capacity/v1','git_sha':sha,'observed_at':when.isoformat().replace('+00:00','Z'),'authority':'independent-management-storage','repository_ref':'management://database-backup-repository','filesystem':{'path':'/independent-storage/pgbackrest/liqi','total_bytes':int(available)+int(reserved)+1024**3,'used_bytes':1024**3,'available_bytes':int(available),'reserved_bytes':int(reserved)},'transport':{'kind':'mutual-tls-over-wireguard','port':8432,'publicly_exposed':False},'status':'passed'}
p=Path(output); p.write_text(json.dumps(doc,indent=2)+'\n',encoding='utf-8'); Path(str(p)+'.sha256').write_text(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n",encoding='utf-8'); p.chmod(0o600); Path(str(p)+'.sha256').chmod(0o600)
PY
}
write_capacity "$temporary/capacity.json" 107374182400 21474836480
LIQI_SOURCE_GIT_SHA=$source_sha LIQI_DATABASE_BACKUP_CAPACITY_FILE="$temporary/capacity.json" PSQL="$temporary/psql" FAKE_DATABASE_SIZE=1073741824 "$ROOT_DIR/database/bin/backup-capacity-check.sh" > "$temporary/allowed.json"
python - "$temporary/allowed.json" <<'PY'
import json,sys
v=json.load(open(sys.argv[1],encoding='utf-8')); assert v['allowed'] is True and v['reason']=='within-independent-repository-capacity'
PY
write_capacity "$temporary/small.json" 3221225472 2147483648
set +e
LIQI_SOURCE_GIT_SHA=$source_sha LIQI_DATABASE_BACKUP_CAPACITY_FILE="$temporary/small.json" PSQL="$temporary/psql" FAKE_DATABASE_SIZE=2147483648 "$ROOT_DIR/database/bin/backup-capacity-check.sh" > "$temporary/blocked.json"
rc=$?
set -e
[[ "$rc" -eq 75 ]]
python - "$temporary/blocked.json" <<'PY'
import json,sys
v=json.load(open(sys.argv[1],encoding='utf-8')); assert v['allowed'] is False and v['reason']=='independent-repository-capacity-insufficient'
PY
write_capacity "$temporary/stale.json" 107374182400 21474836480 stale
set +e
LIQI_SOURCE_GIT_SHA=$source_sha LIQI_DATABASE_BACKUP_CAPACITY_FILE="$temporary/stale.json" PSQL="$temporary/psql" FAKE_DATABASE_SIZE=1 "$ROOT_DIR/database/bin/backup-capacity-check.sh" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" -ne 0 ]]
printf '%s\n' '{"validation":"database-backup-capacity-v1","freshEvidenceAccepted":true,"insufficientCapacityBlocked":true,"staleEvidenceRejected":true,"passed":true}'
