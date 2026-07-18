#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
source_revision=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

cat > "$temporary/database-state.json" <<'JSON'
{"migrationVersion":8,"obanMigrationVersion":14}
JSON

blocked_output="$temporary/blocked.json"
set +e
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_status_v1.py" \
  --database-state "$temporary/database-state.json" \
  --source-revision "$source_revision" \
  --release-id source-only-v1 \
  > "$blocked_output"
blocked_status=$?
set -e
[[ "$blocked_status" -eq 1 ]] || { echo "missing evidence must block recovery status" >&2; exit 1; }
python - "$blocked_output" <<'PY'
import json, sys
value = json.loads(open(sys.argv[1], encoding="utf-8").read())
assert value["status"] == "blocked"
assert value["backup"]["status"] == "blocked"
assert value["restore"]["status"] == "blocked"
assert value["migrationVersion"] == 8
PY

python - "$ROOT_DIR" "$temporary" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
temporary = pathlib.Path(sys.argv[2])
backup = json.loads((root / "contracts/platform/database-backup-status-v0.example.json").read_text())
backup["migrationVersion"] = 8
backup["latestBackup"]["ageSeconds"] = 120
backup["archive"]["secondsSinceLastArchive"] = 120
backup["archive"]["failedCount"] = 0
backup["recoveryReady"] = True
(temporary / "backup.json").write_text(json.dumps(backup, indent=2) + "\n", newline="\n")
restore = json.loads((root / "contracts/platform/database-restore-result-v0.example.json").read_text())
restore["success"] = True
restore["durationSeconds"] = 720
for check in restore["checks"]:
    check["passed"] = True
    if check["name"] == "migration-version":
        check["expected"] = "8"
        check["actual"] = "8"
(temporary / "restore.json").write_text(json.dumps(restore, indent=2) + "\n", newline="\n")
PY
sha256sum "$temporary/backup.json" > "$temporary/backup.json.sha256"
sha256sum "$temporary/restore.json" > "$temporary/restore.json.sha256"

passed_output="$temporary/passed.json"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_status_v1.py" \
  --database-state "$temporary/database-state.json" \
  --source-revision "$source_revision" \
  --release-id source-only-v1 \
  --backup-status "$temporary/backup.json" \
  --backup-status-checksum "$temporary/backup.json.sha256" \
  --restore-result "$temporary/restore.json" \
  --restore-result-checksum "$temporary/restore.json.sha256" \
  --restored-source-revision "$source_revision" \
  > "$passed_output"
python - "$passed_output" <<'PY'
import json, sys
value = json.loads(open(sys.argv[1], encoding="utf-8").read())
assert value["status"] == "passed"
assert value["backup"]["metadataChecksumVerified"] is True
assert value["backup"]["freshnessSeconds"] == 120
assert value["restore"]["invariantsPassed"] is True
assert value["restore"]["restoredMigrationVersion"] == 8
assert value["restore"]["restoredSourceRevision"] == value["sourceRevision"]
assert value["targets"] == {"rpoSeconds": 300, "rtoSeconds": 3600}
PY

cp "$temporary/backup.json" "$temporary/tampered-backup.json"
printf ' ' >> "$temporary/tampered-backup.json"
set +e
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_status_v1.py" \
  --database-state "$temporary/database-state.json" \
  --source-revision "$source_revision" \
  --release-id source-only-v1 \
  --backup-status "$temporary/tampered-backup.json" \
  --backup-status-checksum "$temporary/backup.json.sha256" \
  >/dev/null 2>&1
tampered_status=$?
set -e
[[ "$tampered_status" -ne 0 ]] || { echo "tampered evidence must fail" >&2; exit 1; }
printf '%s\n' '{"test":"recovery-status-v1","blockedWithoutEvidence":true,"passedWithChecksummedEvidence":true,"tamperRejected":true,"passed":true}'
