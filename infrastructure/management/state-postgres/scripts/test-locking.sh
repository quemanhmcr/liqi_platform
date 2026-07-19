#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command tofu; require_command python
output=''; while (($#)); do case "$1" in --output) output=${2:?}; shift 2;; *) fail "unknown argument: $1";; esac; done
[ -n "$output" ] || fail '--output is required'
: "${PG_CONN_STR:?PG_CONN_STR is required in the protected shell}"
: "${TF_ENCRYPTION:?TF_ENCRYPTION is required in the protected shell}"
[[ "$PG_CONN_STR" == *sslmode=verify-full* ]] || fail 'PG_CONN_STR must enforce sslmode=verify-full'
root=$(cd "$(dirname "$0")/.." && pwd); stack="$root/lock-test"
export PG_SCHEMA_NAME=${PG_LOCK_TEST_SCHEMA:-opentofu_v1_locktest}
export PG_SKIP_SCHEMA_CREATION=false PG_SKIP_TABLE_CREATION=false PG_SKIP_INDEX_CREATION=false
# The OpenTofu pg backend consumes PG_CONN_STR and PGPASSWORD but rejects
# libpq service/passfile variables that PostgreSQL administration tools accept.
unset PGSERVICEFILE PGPASSFILE
export TF_DATA_DIR=$(mktemp -d); a=$(mktemp); b=$(mktemp)
cleanup(){ rm -rf "$TF_DATA_DIR" "$a" "$b"; }; trap cleanup EXIT
tofu -chdir="$stack" init -reconfigure -input=false >/dev/null
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
tofu -chdir="$stack" apply -auto-approve -input=false -var=hold_seconds=20 >"$a" 2>&1 & pid=$!
sleep 3
set +e; tofu -chdir="$stack" apply -auto-approve -input=false -lock-timeout=5s -var=hold_seconds=1 >"$b" 2>&1; rc=$?; set -e
wait "$pid"; [ "$rc" -ne 0 ] || fail 'second contender unexpectedly acquired the state lock'
grep -Eqi 'lock|state' "$b" || fail 'second contender failed without lock evidence'
python - "$output" "$started" "$rc" <<'PY2'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"schema_version":"liqi.infrastructure.state-lock-result/v1","observed_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"started_at":sys.argv[2],"mechanism":"postgresql-advisory-locks","contender_rejected":True,"contender_exit_code":int(sys.argv[3]),"status":"passed"},indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY2
protect_file "$output" 600; printf 'state_lock_evidence=%s
' "$output"
