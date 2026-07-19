#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command tofu; require_command python; require_command psql; require_command createdb; require_command dropdb
output=''; while (($#)); do case "$1" in --output) output=${2:?}; shift 2;; *) fail "unknown argument: $1";; esac; done
[ -n "$output" ] || fail '--output is required'
: "${PG_CONN_STR:?PG_CONN_STR is required in the protected shell}"
: "${TF_ENCRYPTION:?TF_ENCRYPTION is required in the protected shell}"
: "${STATE_ADMIN_SERVICE:?STATE_ADMIN_SERVICE is required}"
[[ "$PG_CONN_STR" == *sslmode=verify-full* ]] || fail 'PG_CONN_STR must enforce sslmode=verify-full'
root=$(cd "$(dirname "$0")/.." && pwd); stack="$root/lock-test"
live_database=$STATE_DATABASE
lock_database=${PG_LOCK_TEST_DATABASE:-liqi_infra_state_locktest}; validate_identifier "$lock_database"
[ "$lock_database" != "$live_database" ] || fail 'lock test database must never be the live state database'
lock_schema=${PG_LOCK_TEST_SCHEMA:-opentofu_v1_locktest}; validate_identifier "$lock_schema"
owner_role=${STATE_OWNER_ROLE:-liqi_state_admin}; validate_identifier "$owner_role"
admin_service_file=$PGSERVICEFILE
PGSERVICEFILE="$admin_service_file" dropdb --if-exists --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" "$lock_database" >/dev/null
PGSERVICEFILE="$admin_service_file" createdb --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" --owner="$owner_role" "$lock_database"
PGSERVICEFILE="$admin_service_file" psql "service=$STATE_ADMIN_SERVICE dbname=$lock_database" -v ON_ERROR_STOP=1 -v s="$lock_schema" -v r="$STATE_ROLE" <<'SQL' >/dev/null
SELECT format('CREATE SCHEMA %I AUTHORIZATION %I',:'s',current_user) \gexec
SELECT format('GRANT USAGE,CREATE ON SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT CREATE ON SCHEMA public TO %I',:'r') \gexec
SQL
PG_CONN_STR=$(python - "$PG_CONN_STR" "$lock_database" <<'PY2'
import sys
from urllib.parse import urlsplit,urlunsplit
value,database=sys.argv[1:]
parts=urlsplit(value)
if parts.scheme not in {'postgres','postgresql'} or not parts.netloc:
 raise SystemExit('lock test requires a PostgreSQL connection URI')
print(urlunsplit((parts.scheme,parts.netloc,'/'+database,parts.query,parts.fragment)))
PY2
)
export PG_CONN_STR STATE_DATABASE=$lock_database PG_SCHEMA_NAME=$lock_schema
export PG_SKIP_SCHEMA_CREATION=false PG_SKIP_TABLE_CREATION=false PG_SKIP_INDEX_CREATION=false
# The OpenTofu pg backend consumes PG_CONN_STR and PGPASSWORD but rejects
# libpq service/passfile variables that PostgreSQL administration tools accept.
unset PGSERVICEFILE PGPASSFILE
export TF_DATA_DIR=$(mktemp -d); a=$(mktemp); b=$(mktemp); pid=''
cleanup(){
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then kill "$pid" >/dev/null 2>&1 || true; wait "$pid" >/dev/null 2>&1 || true; fi
  rm -rf "$TF_DATA_DIR" "$a" "$b"
  PGSERVICEFILE="$admin_service_file" dropdb --if-exists --force --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" "$lock_database" >/dev/null 2>&1 || true
}; trap cleanup EXIT
tofu -chdir="$stack" init -reconfigure -input=false >/dev/null
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
tofu -chdir="$stack" apply -auto-approve -input=false -var=hold_seconds=20 >"$a" 2>&1 & pid=$!
sleep 3
set +e; tofu -chdir="$stack" apply -auto-approve -input=false -lock-timeout=5s -var=hold_seconds=1 >"$b" 2>&1; rc=$?; set -e
wait "$pid"; pid=''; [ "$rc" -ne 0 ] || fail 'second contender unexpectedly acquired the state lock'
grep -Eqi 'lock|state' "$b" || fail 'second contender failed without lock evidence'
python - "$output" "$started" "$rc" <<'PY2'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"schema_version":"liqi.infrastructure.state-lock-result/v1","observed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"started_at":sys.argv[2],"mechanism":"postgresql-advisory-locks","contender_rejected":True,"contender_exit_code":int(sys.argv[3]),"isolated_database":True,"status":"passed"},indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY2
protect_file "$output" 600; printf 'state_lock_evidence=%s
' "$output"
