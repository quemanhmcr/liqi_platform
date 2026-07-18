#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CAPACITY_FILE=${LIQI_DATABASE_BACKUP_CAPACITY_FILE:-/run/liqi/management/database-backup-capacity-v1.json}
CAPACITY_CHECKSUM=${LIQI_DATABASE_BACKUP_CAPACITY_CHECKSUM_FILE:-$CAPACITY_FILE.sha256}
PSQL=${PSQL:-psql}
: "${LIQI_SOURCE_GIT_SHA:?LIQI_SOURCE_GIT_SHA is required}"
secure_evidence_file() {
  local path=$1 label=$2
  [[ -f "$path" && ! -L "$path" && -r "$path" ]] || { echo "$label must be a readable regular non-symlink file" >&2; exit 75; }
  if find "$path" -maxdepth 0 -perm /022 -print -quit | grep -q .; then
    echo "$label must not be group/world writable" >&2
    exit 75
  fi
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) ;;
    *) [[ $(stat -c '%u' "$path") -eq 0 ]] || { echo "$label must be root-owned" >&2; exit 75; } ;;
  esac
}
secure_evidence_file "$CAPACITY_FILE" 'capacity evidence'
secure_evidence_file "$CAPACITY_CHECKSUM" 'capacity evidence checksum'
database_bytes=$($PSQL --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 --host="${PGHOST:-/run/postgresql}" --username="${PGUSER:-postgres}" --dbname="${PGDATABASE:-liqi}" -c "SELECT pg_database_size(current_database())")
PYTHONDONTWRITEBYTECODE=1 python - "$ROOT_DIR/contracts/database/backup-repository-capacity-v1.schema.json" "$CAPACITY_FILE" "$CAPACITY_CHECKSUM" "$LIQI_SOURCE_GIT_SHA" "$database_bytes" <<'PY'
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
schema_path,evidence_path,checksum_path,git_sha,database_bytes=sys.argv[1:]
evidence_file=Path(evidence_path); tokens=Path(checksum_path).read_text(encoding='utf-8').split()
if len(tokens)<2 or tokens[0].lower()!=hashlib.sha256(evidence_file.read_bytes()).hexdigest(): raise SystemExit('capacity evidence checksum mismatch')
if Path(tokens[1].lstrip('*')).name!=evidence_file.name: raise SystemExit('capacity evidence checksum filename mismatch')
doc=json.loads(evidence_file.read_text(encoding='utf-8')); errors=list(Draft202012Validator(json.loads(Path(schema_path).read_text(encoding='utf-8')),format_checker=FormatChecker()).iter_errors(doc))
if errors: raise SystemExit(f'capacity evidence contract invalid: {errors[0].message}')
if doc['git_sha']!=git_sha: raise SystemExit('capacity evidence source SHA mismatch')
observed=datetime.fromisoformat(doc['observed_at'].replace('Z','+00:00')).astimezone(timezone.utc); age=max(0,int((datetime.now(timezone.utc)-observed).total_seconds()))
if age>900: raise SystemExit('capacity evidence is stale')
fs=doc['filesystem']; db=int(database_bytes); safety=max(1073741824,db); effective=max(0,int(fs['available_bytes'])-int(fs['reserved_bytes'])); allowed=effective>=db+safety
result={'schema_version':'liqi.database.backup-capacity-check/v1','allowed':allowed,'reason':'within-independent-repository-capacity' if allowed else 'independent-repository-capacity-insufficient','database_bytes':db,'safety_margin_bytes':safety,'available_after_reserve_bytes':effective,'capacity_evidence_age_seconds':age,'repository_ref':doc['repository_ref']}
print(json.dumps(result,separators=(',',':')))
raise SystemExit(0 if allowed else 75)
PY
