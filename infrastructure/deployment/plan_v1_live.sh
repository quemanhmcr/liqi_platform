#!/usr/bin/env bash
set -euo pipefail
umask 077
usage(){ cat >&2 <<'EOF'
Usage: plan_v1_live.sh --state-backend-evidence FILE --var-file FILE --output-dir DIR [--capacity-profile a1-target|e5-temporary] [--adopt-existing --adoption-result FILE] [--approved-apply-plan --approval-reference REF] [--allow-reserved-ip]

Read-only against OCI. Uses the independent PostgreSQL OpenTofu backend and produces an exact saved plan plus validation evidence.
Required protected environment: TF_ENCRYPTION, PG_CONN_STR, PG_SCHEMA_NAME=opentofu_v1_live, PG_SKIP_SCHEMA_CREATION=true, PG_SKIP_TABLE_CREATION=true, PG_SKIP_INDEX_CREATION=true, and OCI provider authentication.
EOF
exit 64; }
state_evidence=''; adoption_result=''; var_file=''; output_dir=''; mode=plan; approval_reference=''; allow_reserved_ip=false; capacity_profile=a1-target; plan_mode=initial-create
while (($#)); do case "$1" in
 --state-backend-evidence) state_evidence=${2:?}; shift 2;; --var-file) var_file=${2:?}; shift 2;; --output-dir) output_dir=${2:?}; shift 2;;
 --capacity-profile) capacity_profile=${2:?}; shift 2;; --adopt-existing) plan_mode=adopt-existing; shift;; --adoption-result) adoption_result=${2:?}; shift 2;;
 --approved-apply-plan) mode=approved-apply; shift;; --approval-reference) approval_reference=${2:?}; shift 2;; --allow-reserved-ip) allow_reserved_ip=true; shift;; *) usage;; esac; done
[[ -f "$state_evidence" && -f "$var_file" && -n "$output_dir" ]] || usage
[[ -n "${TF_ENCRYPTION:-}" ]] || { echo 'TF_ENCRYPTION is required; plaintext state/plan is forbidden' >&2; exit 65; }
[[ -n "${PG_CONN_STR:-}" && "$PG_CONN_STR" == *sslmode=verify-full* ]] || { echo 'PG_CONN_STR with sslmode=verify-full is required' >&2; exit 65; }
[[ "${PG_SCHEMA_NAME:-}" == opentofu_v1_live ]] || { echo 'PG_SCHEMA_NAME must be opentofu_v1_live' >&2; exit 65; }
for name in PG_SKIP_SCHEMA_CREATION PG_SKIP_TABLE_CREATION PG_SKIP_INDEX_CREATION; do [[ "${!name:-}" == true ]] || { echo "$name must be true after backend bootstrap finalization" >&2; exit 65; }; done
[[ "$mode" != approved-apply || ${#approval_reference} -ge 3 ]] || { echo 'approved apply plan requires --approval-reference' >&2; exit 65; }
[[ "$mode" != plan || -z "$approval_reference" ]] || { echo '--approval-reference is valid only with --approved-apply-plan' >&2; exit 65; }
[[ "$capacity_profile" == a1-target || "$capacity_profile" == e5-temporary ]] || { echo 'invalid --capacity-profile' >&2; exit 65; }
[[ "$mode" != approved-apply || "$capacity_profile" == e5-temporary ]] || { echo 'approved apply is enabled only for e5-temporary in this source revision' >&2; exit 65; }
[[ "$plan_mode" != adopt-existing || "$capacity_profile" == e5-temporary ]] || { echo 'adopt-existing is supported only for e5-temporary' >&2; exit 65; }
[[ "$mode" != approved-apply || "$plan_mode" == adopt-existing ]] || { echo 'approved apply requires an adopt-existing plan' >&2; exit 65; }
[[ "$plan_mode" != adopt-existing || -f "$adoption_result" ]] || { echo '--adoption-result is required with --adopt-existing' >&2; exit 65; }
[[ "$plan_mode" != initial-create || -z "$adoption_result" ]] || { echo '--adoption-result is valid only with --adopt-existing' >&2; exit 65; }
root=$(git rev-parse --show-toplevel); cd "$root"; [[ -z "$(git status --porcelain --untracked-files=all)" ]] || { echo 'clean exact-SHA worktree is required for a live plan' >&2; exit 66; }
git_sha=$(git rev-parse HEAD); env_dir="$root/infrastructure/opentofu/environments/v1-live"; validator="$root/infrastructure/validation/validate_v1_plan.py"; schema="$root/contracts/infrastructure/state-backend-evidence-v1.schema.json"
python - "$state_evidence" "$schema" "$git_sha" <<'PY'
import json,sys
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
evidence=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')); schema=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(evidence))
if errors: raise SystemExit(f'invalid state backend evidence: {errors[0].message}')
if evidence.get('git_sha')!=sys.argv[3]: raise SystemExit('state backend evidence Git SHA mismatch')
PY
[[ "$plan_mode" != adopt-existing ]] || python "$root/infrastructure/validation/validate_adoption_result.py" "$adoption_result" --git-sha "$git_sha" >/dev/null
mkdir -p "$output_dir"; output_dir=$(cd "$output_dir" && pwd); export TF_DATA_DIR="$output_dir/tfdata"
plan_file="$output_dir/v1-live.tfplan"; plan_json="$output_dir/v1-live.plan.json"; validation="$output_dir/v1-live.plan-validation.json"; evidence="$output_dir/v1-live.plan-result.json"
tofu -chdir="$env_dir" init -input=false -reconfigure >"$output_dir/tofu-init.log" 2>&1
args=(-input=false -lock=true -lock-timeout=60s -out="$plan_file" -var-file="$var_file" -var="source_git_sha=$git_sha" -var="operation_mode=$mode" -var="apply_approval_reference=$approval_reference" -var="capacity_profile=$capacity_profile")
[[ "$allow_reserved_ip" == true ]] && args+=(-var=enable_reserved_public_ip=true -var=acknowledge_reserved_public_ip=true)
tofu -chdir="$env_dir" plan "${args[@]}" >"$output_dir/tofu-plan.log" 2>&1
tofu -chdir="$env_dir" show -json "$plan_file" >"$plan_json" 2>"$output_dir/tofu-show.log"
vargs=("$plan_json" --mode "$mode" --capacity-profile "$capacity_profile" --plan-mode "$plan_mode" --output "$validation"); [[ "$allow_reserved_ip" == true ]] && vargs+=(--allow-reserved-ip); python "$validator" "${vargs[@]}" >/dev/null
python - "$evidence" "$git_sha" "$mode" "$approval_reference" "$capacity_profile" "$plan_mode" "$plan_file" "$plan_json" "$validation" "$state_evidence" "$adoption_result" "$var_file" "$TF_DATA_DIR" <<'PY'
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
out,git_sha,mode,approval,capacity_profile,plan_mode,plan,plan_json,validation,state_evidence,adoption_result,var_file,tf_data=sys.argv[1:]
doc={"schema_version":"liqi.infrastructure.plan-result/v1","environment":"v1-live","git_sha":git_sha,"mode":mode,"capacity_profile":capacity_profile,"plan_mode":plan_mode,"approval_reference":approval or None,"created_at":datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),"saved_plan":{"path":plan,"sha256":sha(plan)},"plan_json":{"path":plan_json,"sha256":sha(plan_json)},"validation":{"path":validation,"sha256":sha(validation),"status":"passed"},"inputs":{"state_backend_evidence_sha256":sha(state_evidence),"adoption_result_sha256":sha(adoption_result) if adoption_result else None,"var_file_sha256":sha(var_file)},"state_backend":{"kind":"postgresql-self-hosted","schema":"opentofu_v1_live","tls":"verify-full"},"tf_data_dir":tf_data,"oci_mutation_performed":False}
Path(out).write_text(json.dumps(doc,indent=2,sort_keys=True)+'\n',encoding='utf-8')
PY
chmod 600 "$output_dir"/* 2>/dev/null || true
printf 'validated read-only V1 plan: %s\n' "$evidence"
