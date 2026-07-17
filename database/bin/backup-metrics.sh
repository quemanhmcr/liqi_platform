#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
status=$("$ROOT_DIR/database/bin/backup-status.sh")
PYTHONDONTWRITEBYTECODE=1 python - "$status" <<'PY'
import json, sys
status = json.loads(sys.argv[1])
latest = status.get("latestBackup")
print(f"liqi_database_backup_age_seconds {-1 if latest is None else latest['ageSeconds']}")
PY
