#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command pg_restore; require_command createdb; require_command dropdb; require_command gpg; require_command sha256sum; require_command python; require_command psql
manifest=''; output=''; while (($#)); do case "$1" in --manifest) manifest=${2:?}; shift 2;; --output) output=${2:?}; shift 2;; *) fail "unknown argument: $1";; esac; done
[ -f "$manifest" ] || fail '--manifest must identify an existing backup manifest'; [ -n "$output" ] || fail '--output is required'
: "${STATE_ADMIN_SERVICE:?STATE_ADMIN_SERVICE is required}"; : "${STATE_BACKUP_PASSPHRASE_FILE:?STATE_BACKUP_PASSPHRASE_FILE is required}"; require_file_0600 "$STATE_BACKUP_PASSPHRASE_FILE"
dir=$(cd "$(dirname "$manifest")" && pwd); archive_name=$(python -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["archive"])' "$manifest"); archive="$dir/$archive_name"; [ -f "$archive" ] || fail 'encrypted archive not found'; sha256sum -c "$archive.sha256"
target="${STATE_DATABASE}_restore_$(date -u +%s)"; validate_identifier "$target"; tmp=$(mktemp)
cleanup(){ rm -f "$tmp"; dropdb --if-exists --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" "$target" >/dev/null 2>&1 || true; }; trap cleanup EXIT
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$STATE_BACKUP_PASSPHRASE_FILE" --decrypt --output "$tmp" "$archive"
createdb --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" "$target"; pg_restore --dbname="service=$STATE_ADMIN_SERVICE dbname=$target" --no-owner --exit-on-error "$tmp"
restored=$(psql "service=$STATE_ADMIN_SERVICE dbname=$target" -Atqc "SELECT to_regclass('$STATE_SCHEMA.states') IS NOT NULL"); [ "$restored" = t ] || fail 'isolated restore does not contain the OpenTofu state table'
python - "$output" <<'PY2'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"schema_version":"liqi.infrastructure.state-restore-result/v1","observed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"isolated":True,"state_table_verified":True,"cleanup_required":True,"status":"passed"},indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY2
chmod 600 "$output"; printf 'state_restore_evidence=%s
' "$output"
