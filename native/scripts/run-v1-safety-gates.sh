#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec python "$ROOT_DIR/native/scripts/run_v1_safety_gates.py" "$@"
