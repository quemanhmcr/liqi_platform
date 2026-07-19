#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
COMPOSE_FILE="$ROOT_DIR/containers/local/compose.yaml"
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-liqi-v1-local}
export COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-1}
export LIQI_SOURCE_REVISION=${LIQI_SOURCE_REVISION:-$(git -C "$ROOT_DIR" rev-parse HEAD)}
export LIQI_BUILT_AT=${LIQI_BUILT_AT:-$(git -C "$ROOT_DIR" show -s --format=%cI "$LIQI_SOURCE_REVISION")}
export LIQI_LOCAL_HTTP_PORT=${LIQI_LOCAL_HTTP_PORT:-4100}
export LIQI_LOCAL_STATE_DIR=${LIQI_LOCAL_STATE_DIR:-$ROOT_DIR/.artifacts/local-container}

if [[ ! "$LIQI_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "LIQI_SOURCE_REVISION must be an exact lowercase Git SHA" >&2
  exit 64
fi
if [[ "$(git -C "$ROOT_DIR" rev-parse HEAD)" != "$LIQI_SOURCE_REVISION" ]]; then
  echo "checked-out source differs from LIQI_SOURCE_REVISION" >&2
  exit 65
fi
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=no)" ]]; then
  echo "local container operations require a clean tracked worktree" >&2
  exit 65
fi
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 69; }
docker info >/dev/null 2>&1 || { echo "Docker daemon is unavailable" >&2; exit 69; }

compose() {
  docker compose --file "$COMPOSE_FILE" "$@"
}

wait_healthy() {
  local service=$1
  local timeout=${2:-180}
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    local id status state
    id=$(compose ps --quiet "$service")
    if [[ -n "$id" ]]; then
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")
      state=$(docker inspect --format '{{.State.Status}}' "$id")
      if [[ "$status" == "healthy" ]]; then
        return 0
      fi
      if [[ "$state" == "exited" || "$state" == "dead" ]]; then
        echo "$service exited before becoming healthy" >&2
        return 1
      fi
    fi
    sleep 2
  done
  echo "$service did not become healthy within ${timeout}s" >&2
  return 1
}
