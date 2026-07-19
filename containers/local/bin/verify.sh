#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
output=${LIQI_LOCAL_RESULT_PATH:-$LIQI_LOCAL_STATE_DIR/local-container-result.json}
python "$ROOT_DIR/containers/local/bin/verify-local-stack.py" \
  --compose-file "$COMPOSE_FILE" \
  --state-dir "$LIQI_LOCAL_STATE_DIR" \
  --source-revision "$LIQI_SOURCE_REVISION" \
  --base-url "http://127.0.0.1:${LIQI_LOCAL_HTTP_PORT}" \
  --output "$output"
