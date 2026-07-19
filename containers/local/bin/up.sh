#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

on_error() {
  local status=$?
  echo "local stack startup failed; current service state:" >&2
  compose ps >&2 || true
  compose logs --no-color --tail=120 >&2 || true
  if [[ "${LIQI_LOCAL_KEEP_FAILED:-0}" != "1" ]]; then
    compose down --remove-orphans --timeout 20 >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap on_error ERR

python "$ROOT_DIR/containers/local/bin/materialize-secrets.py" --state-dir "$LIQI_LOCAL_STATE_DIR"
compose config --quiet

compose build pod
compose build pgbouncer
compose build runtime

compose up --detach postgres pod
wait_healthy postgres 120
compose up --no-deps db-init
compose up --detach pgbouncer
wait_healthy pgbouncer 120
compose up --detach runtime
wait_healthy runtime 180
wait_healthy pod 60

compose ps
printf '%s\n' "local stack ready at http://127.0.0.1:${LIQI_LOCAL_HTTP_PORT}"
trap - ERR
