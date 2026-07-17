#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/validate_database_contract.py"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/static/validate_database_source.py"
for script in "$ROOT_DIR"/database/bin/*.sh "$ROOT_DIR"/database/tests/integration/*.sh; do
  bash -n "$script"
done
printf '%s\n' '{"validation":"database-shell-syntax-v0","passed":true}'
