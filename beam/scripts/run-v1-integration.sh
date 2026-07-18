#!/usr/bin/env bash
set -euo pipefail
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in --output) output="$2"; shift 2;; *) echo "unknown argument: $1" >&2; exit 64;; esac
done
[[ -n "$output" ]] || { echo "--output is required" >&2; exit 64; }
: "${LIQI_TEST_DATABASE_URL:?LIQI_TEST_DATABASE_URL is required}"
mkdir -p "$(dirname "$output")"
status=blocked
blocker="Senior 2 migration-8 callable SQL seam is not published; runtime refuses to infer function names from semantic-only contracts."
checks='[{"name":"provider-migration-8-callable-seam","status":"blocked"}]'
if find database/migrations -maxdepth 1 -type f -name '000000000008*' | grep -q . && \
   grep -R -q 'request_probe_v1' database/migrations && \
   grep -R -q 'read_realtime_handoff_v1' database/migrations; then
  status=failed
  blocker="Provider migration 8 appeared, but Senior 1 integration tests have not yet been updated to its exact published function signatures."
  checks='[{"name":"provider-migration-8-callable-seam","status":"passed"},{"name":"runtime-database-integration","status":"failed"}]'
fi
STATUS="$status" BLOCKER="$blocker" CHECKS="$checks" OUTPUT="$output" python - <<'PY'
import json, os, subprocess
from datetime import datetime, timezone
result={
 'schema_version':'runtime-integration-result-v1',
 'git_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
 'observed_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
 'status':os.environ['STATUS'],
 'database_target':'disposable-redacted',
 'checks':json.loads(os.environ['CHECKS']),
 'blockers':[os.environ['BLOCKER']]
}
open(os.environ['OUTPUT'],'w',encoding='utf-8',newline='\n').write(json.dumps(result,indent=2)+'\n')
PY
[[ "$status" == passed ]] || exit 2
