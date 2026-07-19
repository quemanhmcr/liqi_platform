#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "${LIQI_LOCAL_KEEP_RUNNING:-0}" != "1" ]]; then
    LIQI_LOCAL_REMOVE_STATE_SECRETS=1 "$ROOT_DIR/containers/local/bin/down.sh" || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM
"$ROOT_DIR/containers/local/bin/up.sh"
"$ROOT_DIR/containers/local/bin/verify.sh"
