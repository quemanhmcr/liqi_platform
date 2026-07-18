#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
MIX=${MIX:-mix}
if [[ "$MIX" == */* ]]; then
  [[ -f "$MIX" ]] || { echo "required command missing: $MIX" >&2; exit 69; }
else
  command -v "$MIX" >/dev/null 2>&1 || { echo "required command missing: $MIX" >&2; exit 69; }
fi

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
umask 077

mix_path() {
  local path=$1
  if [[ "$MIX" == *.bat || "$MIX" == *.exe ]] && command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$path"
  else
    printf '%s\n' "$path"
  fi
}

canonical_formatter=$(mix_path "$ROOT_DIR/.formatter.exs")

for role in api realtime worker; do
  printf '%s\n' 'local-disposable-trust-only' > "$temporary/$role.password"
done

export LIQI_DATABASE_HOST=${LIQI_DATABASE_HOST:-${PGHOST:-127.0.0.1}}
export LIQI_DATABASE_PORT=${LIQI_DATABASE_PORT:-${PGPORT:-5432}}
export LIQI_DATABASE_NAME=${LIQI_DATABASE_NAME:-${PGDATABASE:-liqi_v1_test}}
export LIQI_DATABASE_API_PASSWORD_FILE=$(mix_path "$temporary/api.password")
export LIQI_DATABASE_REALTIME_PASSWORD_FILE=$(mix_path "$temporary/realtime.password")
export LIQI_DATABASE_WORKER_PASSWORD_FILE=$(mix_path "$temporary/worker.password")
export LIQI_DATABASE_INTEGRATION=1
export MIX_HOME=$(mix_path "$temporary/mix-home")
export HEX_HOME=$(mix_path "$temporary/hex-home")
mkdir -p "$temporary/mix-home" "$temporary/hex-home"
"$MIX" local.hex --force >/dev/null
"$MIX" local.rebar --force >/dev/null

export LIQI_MIX_BUILD_PATH=$(mix_path "$temporary/build")
export LIQI_MIX_DEPS_PATH=$(mix_path "$temporary/deps")
export LIQI_MIX_LOCKFILE=$(mix_path "$temporary/mix.lock")
export ERL_FLAGS="${ERL_FLAGS:-+S 4:4}"

run_app() {
  local app=$1
  local app_dir="$ROOT_DIR/beam/apps/$app"
  (
    cd "$app_dir"
    MIX_ENV=test "$MIX" deps.get
    MIX_ENV=test "$MIX" hex.audit
    MIX_ENV=test "$MIX" format --check-formatted --dot-formatter "$canonical_formatter" mix.exs 'config/**/*.exs' 'lib/**/*.{ex,exs}' 'test/**/*.{ex,exs}'
    MIX_ENV=test "$MIX" deps.compile
    MIX_ENV=test "$MIX" compile --warnings-as-errors
    MIX_ENV=test "$MIX" test --no-compile --warnings-as-errors
  )
}

run_app liqi_persistence
run_app liqi_jobs
printf '%s\n' '{"validation":"beam-database-provider-integration-v1","persistence":true,"oban":true,"passed":true}'
