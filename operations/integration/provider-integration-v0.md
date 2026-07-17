# Provider integration V0

## Source gate

Source CI installs pinned OpenTofu and invokes all published read-only provider commands. Unmerged seams are represented as owner-attributed `blocked` evidence. Senior 4 controls still run and must pass.

## Promotion plan evidence

The manually dispatched provider workflow requires:

- `stage=promotion`;
- `oci_plan_run_id` identifying a prior protected workflow run;
- an artifact named by `oci_plan_artifact_name` containing exactly one file: `oci-plan.json`;
- `oci-plan.json` produced by `tofu show -json` from the reviewed saved plan.

The workflow downloads and validates the plan. It does not run `tofu plan` or use OCI API credentials.

## Compatibility result

`provider-compatibility-result-v0` is separate from provider validator output so ownership remains explicit. A failure identifies the owner, seam, code and required action. It is strict in integration/promotion and `--allow-missing` only in source CI while branches are not merged.
