#!/usr/bin/env bash
set -u
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$output" ]] || { echo "--output is required" >&2; exit 64; }
mkdir -p "$(dirname "$output")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
checks=(); blockers=()
record() { checks+=("$1:$2"); }
write_result() {
  CHECKS="$(printf '%s\n' "${checks[@]:-}")" BLOCKERS="$(printf '%s\n' "${blockers[@]:-}")" STATUS="$1" OUTPUT="$output" python - <<'PY'
import json,os,subprocess
from datetime import datetime,timezone
checks=[]
for line in os.environ.get('CHECKS','').splitlines():
    if ':' in line:
        name,status=line.rsplit(':',1); checks.append({'name':name,'status':status})
result={
 'schema_version':'runtime-integration-result-v1',
 'git_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True,timeout=10).strip(),
 'observed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
 'status':os.environ['STATUS'],
 'database_target':'disposable-redacted',
 'checks':checks,
 'blockers':[line for line in os.environ.get('BLOCKERS','').splitlines() if line]
}
with open(os.environ['OUTPUT'],'w',encoding='utf-8',newline='\n') as handle:
    json.dump(result,handle,indent=2); handle.write('\n')
PY
  OUTPUT="$output" python - <<'PY'
import json,os
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
doc=json.loads(Path(os.environ['OUTPUT']).read_text(encoding='utf-8'))
schema=json.loads(Path('contracts/runtime/runtime-integration-result-v1.schema.json').read_text(encoding='utf-8'))
errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(doc))
if errors: raise SystemExit('; '.join(error.message for error in errors))
PY
}
finish_blocked() { blockers+=("$1"); write_result blocked; exit 2; }
finish_failed() { blockers+=("$1"); write_result failed; exit 1; }
run_step() {
  local name="$1" timeout="$2"; shift 2
  if python beam/scripts/run_bounded.py --timeout "$timeout" -- "$@" >"$tmp/$name.log" 2>&1; then
    record "$name" passed
  else
    record "$name" failed
    tail -n 80 "$tmp/$name.log" >&2 || true
    finish_failed "provider or consumer command failed: $name"
  fi
}

if [[ -z "${LIQI_TEST_DATABASE_URL:-}" ]]; then
  record disposable_target blocked
  finish_blocked "LIQI_TEST_DATABASE_URL is required"
fi
if python beam/scripts/prepare_disposable_database.py --database-url "$LIQI_TEST_DATABASE_URL" --directory "$tmp/input" >"$tmp/target.stdout" 2>"$tmp/target.stderr"; then
  record disposable_target passed
else
  record disposable_target failed
  cat "$tmp/target.stderr" >&2 || true
  finish_failed "disposable database target did not satisfy loopback safety policy"
fi
if ! command -v psql >/dev/null 2>&1; then
  record postgresql_client blocked
  finish_blocked "psql is required for the disposable PostgreSQL integration gate"
fi
record postgresql_client passed

mapfile -t target < <(python - "$tmp/input/target.json" <<'PY'
import json,sys
value=json.load(open(sys.argv[1],encoding='utf-8'))
for key in ('host','port','admin_user','target_database','runtime_config_path'): print(value[key])
PY
)
[[ ${#target[@]} -eq 5 ]] || finish_failed "disposable target metadata is incomplete"
export PGHOST="${target[0]}" PGPORT="${target[1]}" PGUSER="${target[2]}" PGDATABASE=postgres PGSSLMODE=disable
export PGPASSFILE="$tmp/empty-pgpass"; : >"$PGPASSFILE"; chmod 600 "$PGPASSFILE" 2>/dev/null || true
unset PGPASSWORD PGSERVICE PGSERVICEFILE
export LIQI_TEST_DATABASE="${target[3]}"

run_step provider_cluster_roles 120 psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --file=database/bootstrap/00_cluster_roles.sql
run_step provider_test_database 120 psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set="database_name=$LIQI_TEST_DATABASE" --file=database/bootstrap/10_create_database.sql
run_step provider_role_settings 120 psql --no-psqlrc --set=ON_ERROR_STOP=1 --dbname=postgres --set="database_name=$LIQI_TEST_DATABASE" --file=database/bootstrap/20_role_settings.sql
export PGDATABASE="$LIQI_TEST_DATABASE"
run_step provider_migrations 600 bash database/bin/migrate.sh

unset LIQI_API_DATABASE_SECRET_REF LIQI_REALTIME_DATABASE_SECRET_REF LIQI_WORKER_DATABASE_SECRET_REF
export LIQI_RUNTIME_CONFIG_PATH="${target[4]}" LIQI_DATABASE_INTEGRATION=1 MIX_ENV=test
export LIQI_TEST_ENDPOINT_SECRET=integration-endpoint-secret LIQI_TEST_PROBE_TOKEN=integration-probe-token LIQI_TEST_DRAIN_TOKEN=integration-drain-token
run_step root_consumer_integration 600 mix test beam/test/liqi/persistence/database_provider_integration_test.exs --seed 0
write_result passed
