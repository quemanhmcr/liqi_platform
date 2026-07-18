#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'USAGE'
Usage: plan_v1_live.sh --backend-config FILE --var-file FILE --output-dir DIR [--approved-apply-plan --approval-reference REF] [--allow-reserved-ip]

Read-only against OCI. Produces a saved OpenTofu plan, JSON plan, validation result,
and a plan evidence document. It never runs tofu apply.
Required environment: TF_ENCRYPTION, AWS_SHARED_CREDENTIALS_FILE (for OCI S3 backend), and OCI provider authentication.
USAGE
  exit 64
}

backend_config=''
var_file=''
output_dir=''
mode='plan'
approval_reference=''
allow_reserved_ip='false'
while (($#)); do
  case "$1" in
    --backend-config) backend_config="${2:?}"; shift 2 ;;
    --var-file) var_file="${2:?}"; shift 2 ;;
    --output-dir) output_dir="${2:?}"; shift 2 ;;
    --approved-apply-plan) mode='approved-apply'; shift ;;
    --approval-reference) approval_reference="${2:?}"; shift 2 ;;
    --allow-reserved-ip) allow_reserved_ip='true'; shift ;;
    *) usage ;;
  esac
done
[[ -n "$backend_config" && -f "$backend_config" && -n "$var_file" && -f "$var_file" && -n "$output_dir" ]] || usage
[[ -n "${TF_ENCRYPTION:-}" ]] || { echo 'TF_ENCRYPTION is required; plaintext state/plan is forbidden' >&2; exit 65; }
[[ -n "${AWS_SHARED_CREDENTIALS_FILE:-}" && -f "$AWS_SHARED_CREDENTIALS_FILE" ]] || { echo 'AWS_SHARED_CREDENTIALS_FILE is required for the partial OCI S3 backend' >&2; exit 65; }
if [[ "$mode" == 'approved-apply' && ${#approval_reference} -lt 3 ]]; then
  echo 'approved apply plan requires --approval-reference' >&2
  exit 65
fi
if [[ "$mode" == 'plan' && -n "$approval_reference" ]]; then
  echo '--approval-reference is valid only with --approved-apply-plan' >&2
  exit 65
fi

root="$(git rev-parse --show-toplevel)"
cd "$root"
git diff --quiet && git diff --cached --quiet || { echo 'tracked worktree changes are forbidden for an exact-SHA live plan' >&2; exit 66; }
git_sha="$(git rev-parse HEAD)"
env_dir="$root/infrastructure/opentofu/environments/v1-live"
validator="$root/infrastructure/validation/validate_v1_plan.py"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
export TF_DATA_DIR="$output_dir/tfdata"

plan_file="$output_dir/v1-live.tfplan"
plan_json="$output_dir/v1-live.plan.json"
validation="$output_dir/v1-live.plan-validation.json"
evidence="$output_dir/v1-live.plan-result.json"

init_log="$output_dir/tofu-init.log"
plan_log="$output_dir/tofu-plan.log"
show_log="$output_dir/tofu-show.log"

tofu -chdir="$env_dir" init \
  -input=false \
  -reconfigure \
  -backend-config="$backend_config" >"$init_log" 2>&1

plan_args=(
  -input=false
  -lock=true
  -lock-timeout=60s
  -out="$plan_file"
  -var-file="$var_file"
  -var="source_git_sha=$git_sha"
  -var="operation_mode=$mode"
  -var="apply_approval_reference=$approval_reference"
)
if [[ "$allow_reserved_ip" == 'true' ]]; then
  plan_args+=( -var='enable_reserved_public_ip=true' -var='acknowledge_reserved_public_ip=true' )
fi
tofu -chdir="$env_dir" plan "${plan_args[@]}" >"$plan_log" 2>&1
tofu -chdir="$env_dir" show -json "$plan_file" >"$plan_json" 2>"$show_log"

validate_args=("$plan_json" --mode "$mode" --output "$validation")
[[ "$allow_reserved_ip" == 'true' ]] && validate_args+=(--allow-reserved-ip)
python "$validator" "${validate_args[@]}" >/dev/null

python - "$evidence" "$git_sha" "$mode" "$approval_reference" "$plan_file" "$plan_json" "$validation" "$backend_config" "$var_file" "$TF_DATA_DIR" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

def sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

out, git_sha, mode, approval, plan_file, plan_json, validation, backend, var_file, tf_data = sys.argv[1:]
doc = {
    "schema_version": "liqi.infrastructure.plan-result/v1",
    "environment": "v1-live",
    "git_sha": git_sha,
    "mode": mode,
    "approval_reference": approval or None,
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "saved_plan": {"path": plan_file, "sha256": sha(plan_file)},
    "plan_json": {"path": plan_json, "sha256": sha(plan_json)},
    "validation": {"path": validation, "sha256": sha(validation), "status": "passed"},
    "inputs": {"backend_config_sha256": sha(backend), "var_file_sha256": sha(var_file)},
    "tf_data_dir": tf_data,
    "oci_mutation_performed": False,
}
Path(out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
chmod 0600 "$plan_file" "$plan_json" "$validation" "$evidence" "$init_log" "$plan_log" "$show_log"
printf 'validated read-only V1 plan: %s\n' "$evidence"
