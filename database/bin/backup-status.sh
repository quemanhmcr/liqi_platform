#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PGBACKREST_COMMAND=${LIQI_PGBACKREST_COMMAND:-"$ROOT_DIR/database/bin/pgbackrest-command.sh"}
OUTPUT=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || { echo '--output requires a path' >&2; exit 64; }
      OUTPUT=$2
      shift 2
      ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT

"$PGBACKREST_COMMAND" --stanza=liqi --output=json info > "$temporary/info.json"
"$ROOT_DIR/database/bin/backup-verification-state.sh" > "$temporary/state.json"
psql --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --host="${PGHOST:-/run/postgresql}" --username="${PGUSER:-postgres}" --dbname="${PGDATABASE:-liqi}" > "$temporary/archive.json" <<'SQL'
SELECT json_build_object(
    'archivedCount', archived_count,
    'failedCount', failed_count,
    'lastArchivedWal', last_archived_wal,
    'lastArchivedAt', last_archived_time,
    'lastFailedWal', last_failed_wal,
    'lastFailedAt', last_failed_time,
    'secondsSinceLastArchive', CASE WHEN last_archived_time IS NULL THEN NULL ELSE EXTRACT(EPOCH FROM (clock_timestamp() - last_archived_time)) END
)::text
FROM pg_stat_archiver;
SQL

PYTHONDONTWRITEBYTECODE=1 python - "$temporary/info.json" "$temporary/state.json" "$temporary/archive.json" > "$temporary/status-unvalidated.json" <<'PY'
import json, sys, time
from datetime import datetime, timezone
info = json.load(open(sys.argv[1], encoding="utf-8"))
state = json.load(open(sys.argv[2], encoding="utf-8"))
archive = json.load(open(sys.argv[3], encoding="utf-8"))
stanza = next((item for item in info if item.get("name") == "liqi"), {})
backups = stanza.get("backup", [])
latest = max(backups, key=lambda item: item.get("timestamp", {}).get("stop", 0), default=None)
repo_status = stanza.get("status", {})
age = None
if latest:
    age = max(0, time.time() - float(latest.get("timestamp", {}).get("stop", 0)))
archive_age = archive.get("secondsSinceLastArchive")
reasons = []
if repo_status.get("code") not in (0, None): reasons.append("pgbackrest-repository-error")
if latest is None: reasons.append("no-backup")
if state.get("failedMigrationRuns") != 0: reasons.append("failed-migration-run")
if archive.get("failedCount", 0) > 0 and archive.get("lastFailedAt") and (not archive.get("lastArchivedAt") or archive["lastFailedAt"] > archive["lastArchivedAt"]): reasons.append("wal-archive-failure")
if archive_age is None or archive_age > 600: reasons.append("wal-archive-stale")
print(json.dumps({
    "$schema": "./database-backup-status-v0.schema.json",
    "schemaVersion": "database-backup-status-v0",
    "recoveryReady": not reasons,
    "reasons": reasons,
    "latestBackup": None if latest is None else {"label": latest.get("label"), "type": latest.get("type"), "ageSeconds": round(age, 3)},
    "archive": archive,
    "migrationVersion": state.get("migrationVersion"),
    "probe": state.get("probe"),
    "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, separators=(",", ":")))
PY

destination=${OUTPUT:-"$temporary/backup-status-v0.json"}
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" write-backup-status \
  --input "$temporary/status-unvalidated.json" --output "$destination" >/dev/null
cat "$destination"
