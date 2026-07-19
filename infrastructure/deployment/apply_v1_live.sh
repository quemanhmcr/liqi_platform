#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'USAGE'
Usage: apply_v1_live.sh --plan-result FILE --pre-apply-readiness FILE --approval-reference REF --execute

MUTATES OCI by applying the exact saved plan recorded in FILE. No automatic re-plan.
Required environment: TF_ENCRYPTION, protected PostgreSQL backend environment (PG_CONN_STR/PG_SCHEMA_NAME/PG_SKIP_*), and OCI provider authentication.
USAGE
  exit 64
}

plan_result=''
pre_apply_readiness=''
approval_reference=''
execute='false'
while (($#)); do
  case "$1" in
    --plan-result) plan_result="${2:?}"; shift 2 ;;
    --pre-apply-readiness) pre_apply_readiness="${2:?}"; shift 2 ;;
    --approval-reference) approval_reference="${2:?}"; shift 2 ;;
    --execute) execute='true'; shift ;;
    *) usage ;;
  esac
done
[[ -n "$plan_result" && -f "$plan_result" && ! -L "$plan_result" && -n "$pre_apply_readiness" && -f "$pre_apply_readiness" && ! -L "$pre_apply_readiness" && ${#approval_reference} -ge 3 ]] || usage
[[ "$execute" == 'true' ]] || { echo 'refusing OCI mutation without --execute' >&2; exit 65; }
[[ -n "${TF_ENCRYPTION:-}" ]] || { echo 'TF_ENCRYPTION is required' >&2; exit 65; }
[[ -n "${PG_CONN_STR:-}" && "$PG_CONN_STR" == *sslmode=verify-full* ]] || { echo 'PG_CONN_STR with sslmode=verify-full is required' >&2; exit 65; }
[[ "${PG_SCHEMA_NAME:-}" == "opentofu_v1_live" ]] || { echo 'PG_SCHEMA_NAME must be opentofu_v1_live' >&2; exit 65; }
for name in PG_SKIP_SCHEMA_CREATION PG_SKIP_TABLE_CREATION PG_SKIP_INDEX_CREATION; do
  [[ "${!name:-}" == "true" ]] || { echo "$name must be true" >&2; exit 65; }
done

root="$(git rev-parse --show-toplevel)"
cd "$root"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || { echo 'clean exact-SHA worktree is required for apply' >&2; exit 66; }
current_sha="$(git rev-parse HEAD)"

readarray -t fields < <(python - "$plan_result" "$approval_reference" "$current_sha" "$pre_apply_readiness" "$root" <<'PY'
import hashlib, json, sys
from pathlib import Path
path, approval, current_sha, readiness_path, root = sys.argv[1:]
sys.path.insert(0, root)
from infrastructure.validation import validate_pre_apply_readiness as pre
doc = json.loads(Path(path).read_text(encoding="utf-8"))
if doc.get("schema_version") != "liqi.infrastructure.plan-result/v1": raise SystemExit("invalid plan result schema")
if doc.get("mode") != "approved-apply": raise SystemExit("plan result is not approved-apply mode")
if doc.get("capacity_profile") != "e5-temporary": raise SystemExit("approved apply is restricted to e5-temporary")
if doc.get("plan_mode") != "adopt-existing": raise SystemExit("approved apply requires an adopt-existing saved plan")
inputs = doc.get("inputs", {})
for name in (
    "state_backend_evidence_sha256", "adoption_result_sha256", "pre_apply_readiness_sha256",
    "var_file_sha256", "adoption_manifest_sha256", "linux_release_build_result_sha256",
    "rollback_target_sha256",
):
    if not inputs.get(name): raise SystemExit(f"plan result is not bound to {name}")
if doc.get("approval_reference") != approval: raise SystemExit("approval reference mismatch")
if doc.get("git_sha") != current_sha: raise SystemExit("Git SHA mismatch")
if doc.get("validation", {}).get("status") != "passed": raise SystemExit("plan validation is not passed")
plan = Path(doc["saved_plan"]["path"])
if not plan.is_file(): raise SystemExit("saved plan is missing")
actual = hashlib.sha256(plan.read_bytes()).hexdigest()
if actual != doc["saved_plan"]["sha256"]: raise SystemExit("saved plan digest mismatch")
for field in ("plan_json", "validation"):
    artifact = Path(doc[field]["path"])
    if not artifact.is_file(): raise SystemExit(f"{field} artifact is missing")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != doc[field]["sha256"]: raise SystemExit(f"{field} digest mismatch")
readiness_path = pre.regular(Path(readiness_path), "pre-apply readiness result")
readiness = pre.load(readiness_path, "pre-apply readiness result")
pre.validate_document(readiness, current_sha)
if pre.digest(readiness_path) != inputs["pre_apply_readiness_sha256"]:
    raise SystemExit("pre-apply readiness digest mismatch")
for name in (
    "state_backend_evidence_sha256", "adoption_result_sha256", "var_file_sha256",
    "adoption_manifest_sha256", "linux_release_build_result_sha256", "rollback_target_sha256",
):
    if readiness.get("inputs", {}).get(name) != inputs.get(name):
        raise SystemExit(f"pre-apply readiness/plan binding mismatch: {name}")
print(plan)
print(doc["tf_data_dir"])
print(Path(path).resolve().parent)
PY
)
plan_file="${fields[0]}"
export TF_DATA_DIR="${fields[1]}"
output_dir="${fields[2]}"
env_dir="$root/infrastructure/opentofu/environments/v1-live"
apply_log="$output_dir/tofu-apply.log"
output_json="$output_dir/oci-live-v1.output.json"
apply_result="$output_dir/v1-live.apply-result.json"

tofu -chdir="$env_dir" apply -input=false -lock=true -lock-timeout=60s "$plan_file" >"$apply_log" 2>&1
tofu -chdir="$env_dir" output -json oci_live_v1 >"$output_json"

python - "$apply_result" "$plan_result" "$approval_reference" "$current_sha" "$output_json" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
out, plan_result, approval, git_sha, output_json = sys.argv[1:]
plan = json.loads(Path(plan_result).read_text(encoding="utf-8"))
oci = json.loads(Path(output_json).read_text(encoding="utf-8"))
doc = {
  "schema_version": "liqi.infrastructure.apply-result/v1",
  "environment": "v1-live",
  "git_sha": git_sha,
  "approval_reference": approval,
  "capacity_profile": plan["capacity_profile"],
  "plan_mode": plan["plan_mode"],
  "saved_plan_sha256": plan["saved_plan"]["sha256"],
  "pre_apply_readiness_sha256": plan["inputs"]["pre_apply_readiness_sha256"],
  "linux_release_build_result_sha256": plan["inputs"]["linux_release_build_result_sha256"],
  "rollback_target_sha256": plan["inputs"]["rollback_target_sha256"],
  "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
  "status": "applied",
  "oci_output_sha256": hashlib.sha256(Path(output_json).read_bytes()).hexdigest(),
  "oci_output": oci,
  "oci_mutation_performed": True,
}
Path(out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0600 "$apply_log" "$output_json" "$apply_result"
printf 'approved OCI apply completed: %s\n' "$apply_result"
