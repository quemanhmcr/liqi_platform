#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
COMPOSE_FILE="$ROOT_DIR/containers/local/compose.yaml"
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-liqi-v1-local}
export LIQI_SOURCE_REVISION=${LIQI_SOURCE_REVISION:-$(git -C "$ROOT_DIR" rev-parse HEAD)}
export LIQI_BUILT_AT=${LIQI_BUILT_AT:-$(git -C "$ROOT_DIR" show -s --format=%cI "$LIQI_SOURCE_REVISION")}
export LIQI_LOCAL_HTTP_PORT=${LIQI_LOCAL_HTTP_PORT:-4100}
export LIQI_LOCAL_STATE_DIR=${LIQI_LOCAL_STATE_DIR:-$ROOT_DIR/.artifacts/local-container}
secret_gid_path="$LIQI_LOCAL_STATE_DIR/secrets/endpoint_secret"
if [[ -z "${LIQI_LOCAL_SECRET_GID:-}" ]]; then
  if [[ -f "$secret_gid_path" && ! -L "$secret_gid_path" ]]; then
    export LIQI_LOCAL_SECRET_GID=$(stat --format='%g' "$secret_gid_path")
  else
    export LIQI_LOCAL_SECRET_GID=10001
  fi
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose --file "$COMPOSE_FILE" down --volumes --remove-orphans --timeout 30
fi

if [[ "${LIQI_LOCAL_REMOVE_STATE_SECRETS:-0}" == "1" ]]; then
  rm -f "$LIQI_LOCAL_STATE_DIR/secrets/endpoint_secret"
  rm -f "$LIQI_LOCAL_STATE_DIR/secrets/probe_token"
  rm -f "$LIQI_LOCAL_STATE_DIR/secrets/drain_token"
  rmdir "$LIQI_LOCAL_STATE_DIR/secrets" 2>/dev/null || true
fi
