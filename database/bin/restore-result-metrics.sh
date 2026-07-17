#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${LIQI_RESTORE_RESULT_FILE:?LIQI_RESTORE_RESULT_FILE is required}"
: "${LIQI_RESTORE_RESULT_CHECKSUM_FILE:=${LIQI_RESTORE_RESULT_FILE}.sha256}"
[[ -r "$LIQI_RESTORE_RESULT_FILE" ]] || { echo 'restore result is unavailable' >&2; exit 66; }
[[ -r "$LIQI_RESTORE_RESULT_CHECKSUM_FILE" ]] || { echo 'restore result checksum is unavailable' >&2; exit 66; }
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-restore-result \
  --result "$LIQI_RESTORE_RESULT_FILE" \
  --checksum "$LIQI_RESTORE_RESULT_CHECKSUM_FILE" >/dev/null
PYTHONDONTWRITEBYTECODE=1 python - "$LIQI_RESTORE_RESULT_FILE" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"liqi_database_restore_verification_success {1 if result.get('success') else 0}")
print(f"liqi_database_restore_duration_seconds {float(result.get('durationSeconds', 0))}")
PY
