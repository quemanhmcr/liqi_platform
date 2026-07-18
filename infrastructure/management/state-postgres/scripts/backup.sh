#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command pg_dump; require_command gpg; require_command sha256sum; require_command python
output=''; while (($#)); do case "$1" in --output) output=${2:?}; shift 2;; *) fail "unknown argument: $1";; esac; done
[ -n "$output" ] || fail '--output is required'
: "${STATE_BACKUP_SERVICE:?STATE_BACKUP_SERVICE is required}"; : "${STATE_BACKUP_DIR:?STATE_BACKUP_DIR is required}"; : "${STATE_BACKUP_PASSPHRASE_FILE:?STATE_BACKUP_PASSPHRASE_FILE is required}"
require_file_0600 "$STATE_BACKUP_PASSPHRASE_FILE"; [ -d "$STATE_BACKUP_DIR" ] || fail 'independent backup directory does not exist'
stamp=$(date -u +%Y%m%dT%H%M%SZ); base="$STATE_BACKUP_DIR/${STATE_DATABASE}-${stamp}.dump"; tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
pg_dump --dbname="service=$STATE_BACKUP_SERVICE dbname=$STATE_DATABASE" --format=custom --no-owner --file="$tmp"
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$STATE_BACKUP_PASSPHRASE_FILE" --symmetric --cipher-algo AES256 --output "$base.gpg" "$tmp"
sha256sum "$base.gpg" > "$base.gpg.sha256"
python - "$base.manifest.json" "$base.gpg" <<'PY2'
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
archive=Path(sys.argv[2]); doc={"schema_version":"liqi.infrastructure.state-backup-result/v1","observed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"database":"liqi_infra_state","schema":"opentofu_v1_live","encrypted":True,"off_host_from_oci_application":True,"archive":archive.name,"archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),"status":"passed"}; Path(sys.argv[1]).write_text(json.dumps(doc,indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY2
chmod 400 "$base.gpg" "$base.gpg.sha256" "$base.manifest.json"; cp "$base.manifest.json" "$output"; chmod 600 "$output"; printf 'state_backup_manifest=%s
state_backup_evidence=%s
' "$base.manifest.json" "$output"
