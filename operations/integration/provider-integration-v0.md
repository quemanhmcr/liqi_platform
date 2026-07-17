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

## Runtime provider state

Senior 3 commits `7ed9cc9`, `dd8d643` and `c8d9b96` publish the Rust workspace, runtime capacity/telemetry contracts and provider-owned platform probe. Source CI invokes rustfmt, locked Cargo metadata and the shared runtime operability contract validator without compiling. Build-dependent commands remain `pending-owner-build`; they are not run automatically. The platform probe seam is available, but its owner-built executable must be present on PATH and its realtime checks consume the integrated provider handoff fail-closed.

Owner-run commands, when requested:

```bash
cargo +1.97.1 run --locked -p liqi-platform-tool -- print-validation-manifest
cargo +1.97.1 run --locked -p liqi-platform-tool -- validate-contracts --root .
cargo +1.97.1 clippy --workspace --all-targets --all-features --locked -- -D warnings
cargo +1.97.1 test --workspace --all-targets --all-features --locked
```

Expected evidence is JSON for the first two commands and a zero exit code plus complete logs for clippy/tests. Do not run them through automatic source CI under the current owner-only build rule.

Current runtime promotion blockers are machine-readable:

- missing `runtime-capacity-budget-v0`;
- missing API/realtime/worker `telemetry-v0` capability declarations;
- missing provider-owned `platform-probe-result-v0` runner;
- realtime committed-handoff dependency not yet published by Senior 2.
