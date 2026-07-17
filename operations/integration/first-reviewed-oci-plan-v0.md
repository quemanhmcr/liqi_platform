# First reviewed OCI plan V0

This workflow creates a saved **plan only**. It must not be followed by `tofu apply` without a separate explicit approval.

## Preconditions

- final `main` working tree is clean;
- source readiness and all four project-owner runtime build records are `passed` for the same SHA;
- OCI authentication context, tenancy/compartment, region, availability domain, and pinned Oracle Linux image are reviewed;
- a secure untracked tfvars file contains OCIDs, one public SSH key, and policy flags only—no OCI credentials, private key, password, token, PEM, DSN, or resolved secret;
- `acknowledge_non_always_free_profile=true` is present only after cost review;
- `enable_admin_ssh=false` unless an exact non-world, time-bounded exception is approved;
- no concurrent OpenTofu writer exists;
- plan/state/output locations are outside Git and have restrictive permissions.

## Exact commands

Set reviewed paths; do not use repository-relative paths for sensitive operator inputs:

```bash
root=$(git rev-parse --show-toplevel)
envdir="$root/infrastructure/opentofu/environments/development"
vars=/secure/liqi/v0/development.tfvars
plan=/secure/liqi/v0/development.tfplan
plan_json=/secure/liqi/v0/development.tfplan.json
plan_sha=/secure/liqi/v0/development.tfplan.sha256

cd "$root"
test -f "$vars"
test -z "$(git status --short --untracked-files=no)"
git rev-parse HEAD

python infrastructure/validation/validate_infrastructure.py --with-tofu
python scripts/operations/scan_repository_secrets.py

tofu -chdir="$envdir" init -backend=false -input=false
tofu -chdir="$envdir" validate

tofu -chdir="$envdir" plan \
  -refresh=false \
  -input=false \
  -lock=false \
  -var-file="$vars" \
  -out="$plan"

tofu -chdir="$envdir" show -json "$plan" > "$plan_json"
python infrastructure/validation/validate_oci_plan.py "$plan_json"
sha256sum "$plan" "$plan_json" > "$plan_sha"
```

Expected validator result: one bounded 27-resource create plan for the V0 host contract, public ingress only 80/443, SSH disabled unless explicitly approved, no secret material, exact 4 OCPU/24 GiB shape, 200 GiB boot plus 100 GiB data volume, instance-principal/object-storage policy constraints, and gzip cloud-init below 16 KiB.

## Review record

The approval record must bind:

- exact Git SHA;
- tfvars digest or protected configuration reference;
- saved binary plan digest and plan JSON digest;
- validator output;
- expected resource/action summary;
- cost acknowledgement;
- operator and reviewer identities;
- maintenance window, rollback, and state/locking decision.

No command in this document mutates OCI. `tofu apply` is intentionally absent.
