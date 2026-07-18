#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d); trap 'rm -rf "$temporary"' EXIT
mkdir -p "$temporary/metadata" "$temporary/restore" "$temporary/etc"
cp "$ROOT_DIR/contracts/database/backup-metadata-v1.example.json" "$temporary/metadata/latest.json"
metadata_sha=$(sha256sum "$temporary/metadata/latest.json" | awk '{print $1}')
printf '%s  latest.json\n' "$metadata_sha" > "$temporary/metadata/latest.json.sha256"
metadata_label=$(PYTHONDONTWRITEBYTECODE=1 python -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["backup"]["label"])' "$temporary/metadata/latest.json")
cp "$temporary/metadata/latest.json" "$temporary/metadata/$metadata_label.json"
printf '%s  %s.json\n' "$metadata_sha" "$metadata_label" > "$temporary/metadata/$metadata_label.json.sha256"
PYTHONDONTWRITEBYTECODE=1 python - "$ROOT_DIR/contracts/platform/database-restore-result-v0.example.json" "$temporary/restore/restore-result.json" "$metadata_sha" "$metadata_label" <<'PY'
import json,sys
value=json.load(open(sys.argv[1],encoding='utf-8')); value['backupMetadataSha256']=sys.argv[3]; value['backupLabel']=sys.argv[4]
json.dump(value,open(sys.argv[2],'w',encoding='utf-8'),indent=2); open(sys.argv[2],'a',encoding='utf-8').write('\n')
PY
restore_sha=$(sha256sum "$temporary/restore/restore-result.json" | awk '{print $1}')
printf '%s  restore-result.json\n' "$restore_sha" > "$temporary/restore/restore-result.json.sha256"
PYTHONDONTWRITEBYTECODE=1 python - "$ROOT_DIR/contracts/platform/database-backup-status-v0.example.json" "$temporary/metadata/latest.json" "$temporary/metadata/backup-status-v0.json" <<'PY'
import json,sys
status=json.load(open(sys.argv[1],encoding='utf-8')); metadata=json.load(open(sys.argv[2],encoding='utf-8'))
status['latestBackup']={'label':metadata['backup']['label'],'type':metadata['backup']['type'],'ageSeconds':120}; status['migrationVersion']=metadata['migration']['currentVersion']; status['probe']=metadata['probe']; status['observedAt']=metadata['generatedAt']; status['recoveryReady']=True; status['reasons']=[]
json.dump(status,open(sys.argv[3],'w',encoding='utf-8'),indent=2); open(sys.argv[3],'a',encoding='utf-8').write('\n')
PY
status_sha=$(sha256sum "$temporary/metadata/backup-status-v0.json" | awk '{print $1}')
printf '%s  backup-status-v0.json\n' "$status_sha" > "$temporary/metadata/backup-status-v0.json.sha256"
printf '%s\n' 'LIQI_ENVIRONMENT=development' > "$temporary/etc/provider.env"
LIQI_DATABASE_PROVIDER_ENV_FILE="$temporary/etc/provider.env" LIQI_BACKUP_METADATA_FILE="$temporary/metadata/latest.json" LIQI_RESTORE_RESULT_FILE="$temporary/restore/restore-result.json" LIQI_BACKUP_STATUS_FILE="$temporary/metadata/backup-status-v0.json" "$ROOT_DIR/database/bin/recovery-status.sh" --output "$temporary/recovery-status.json" >/dev/null
python - "$temporary/recovery-status.json" <<'PY'
import json,sys
v=json.load(open(sys.argv[1],encoding='utf-8')); assert v['schema_version']=='recovery-status-v0'; assert v['owner']=='Senior 2'; assert v['environment']=='development'; assert v['backup']['off_host'] is True; assert v['restore_verification']['status']=='passed'; assert v['backup']['evidence_ref'].startswith('pgbackrest://management/')
PY
printf ' ' >> "$temporary/metadata/backup-status-v0.json"
set +e
LIQI_DATABASE_PROVIDER_ENV_FILE="$temporary/etc/provider.env" LIQI_BACKUP_METADATA_FILE="$temporary/metadata/latest.json" LIQI_RESTORE_RESULT_FILE="$temporary/restore/restore-result.json" LIQI_BACKUP_STATUS_FILE="$temporary/metadata/backup-status-v0.json" "$ROOT_DIR/database/bin/recovery-status.sh" --output "$temporary/rejected.json" >/dev/null 2>&1
status=$?
set -e
[[ "$status" -ne 0 ]] || { echo 'corrupt backup status was accepted' >&2; exit 1; }
printf '%s\n' '{"validation":"database-recovery-status-cli-v1","passed":true}'
