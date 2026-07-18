#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec python "$ROOT_DIR/database/tools/run_restore_drill_v1.py" "$@"
