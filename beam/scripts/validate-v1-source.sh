#!/usr/bin/env bash
set -euo pipefail
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
checks=()
run_check() {
  local name="$1"; shift
  if python beam/scripts/run_bounded.py --timeout 600 "$@" >"$tmp/$name.log" 2>&1; then checks+=("$name:passed"); else
    cat "$tmp/$name.log" >&2
    checks+=("$name:failed")
    write_result failed "source check failed: $name"
    exit 1
  fi
}
write_result() {
  local status="$1" blocker="${2:-}"
  ERLANG_OBSERVED="$(erl -noshell -eval 'io:format("~s", [erlang:system_info(otp_release)]), halt().' 2>/dev/null)" ELIXIR_OBSERVED="$(elixir --short-version 2>/dev/null)" CHECKS="$(printf '%s\n' "${checks[@]:-}")" BLOCKER="$blocker" STATUS="$status" OUTPUT="$output" python - <<'PY'
import json, os, subprocess
from datetime import datetime, timezone
checks=[]
for line in os.environ.get('CHECKS','').splitlines():
    if ':' in line:
        name,status=line.rsplit(':',1); checks.append({'name':name,'status':status})
result={
 'schema_version':'runtime-source-result-v1',
 'git_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
 'observed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
 'status':os.environ['STATUS'],
 'toolchain':{
   'erlang':os.environ['ERLANG_OBSERVED'],
   'elixir':os.environ['ELIXIR_OBSERVED']
 },
 'checks':checks,
 'blockers':[os.environ['BLOCKER']] if os.environ.get('BLOCKER') else []
}
open(os.environ['OUTPUT'],'w',encoding='utf-8',newline='\n').write(json.dumps(result,indent=2)+'\n')
PY
}
run_check format -- mix format --check-formatted
run_check compile --env MIX_ENV=test -- mix compile --warnings-as-errors
run_check tests --env MIX_ENV=test -- mix test --seed 0
run_check dependency_audit -- mix hex.audit
run_check shared_contracts -- python scripts/operations/validate_contracts.py
run_check database_contracts -- python database/tests/contract/validate_v1_contracts.py
run_check infrastructure_contracts -- python infrastructure/validation/validate_v1_contracts.py
run_check native_contracts -- python contracts/native/validate_contracts.py
run_check secret_scan -- python scripts/operations/scan_repository_secrets.py
write_result passed
