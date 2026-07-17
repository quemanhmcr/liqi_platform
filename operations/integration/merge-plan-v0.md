# V0 merge and integration plan

All workstreams started from baseline `2d72ce4`. Read-only `git merge-tree` analysis found no content conflict between Senior 4 and Senior 2/3, and only normal scaffold `.gitkeep` removals when Senior 1 adds OpenTofu source.

## 1. Merge Senior 4 control plane

Merge `v0/operability-release` first.

Why:

- Senior 4 is the single writer for `.github/**`, `operations/**`, `scripts/**`, repository instructions and operations contracts.
- Source CI can represent unmerged providers as `blocked` without mock or permissive fallback.
- Senior 1/2/3 can rebase onto the final operational contracts instead of resolving them in a late integration branch.

Expected source readiness immediately after this merge: `blocked`, not `failed`.

## 2. Rebase and merge Senior 1

Before merge, Senior 1 must:

- publish `contracts/platform/infrastructure-capacity-budget-v0.json`;
- align cloud-init journald with the V0 policy: 2 GiB maximum use, 10 GiB keep-free, seven-day retention, 30-second/10,000-message rate limiting and no syslog forwarding;
- keep host output `0.2.0`, staging/install/symlink semantics and read-only plan validation compatible;
- run `python infrastructure/validation/validate_infrastructure.py --with-tofu`.

Do not merge Senior 1 while `provider-compatibility-result-v0` is failed.

## 3. Rebase and merge Senior 2

Senior 2 source validation and `recovery-status-v0` are already directly consumable. Before merge, Senior 2 must:

- move or version restore implementation and database runbooks out of Senior 4-owned `operations/**` into `database/**`;
- update `database-v0` restore command paths accordingly;
- publish provider-owned prepare/restore/verify/cleanup commands for the isolated recovery exercise, or coordinate a versioned recovery-plan command change;
- preserve `contracts/platform/database-capacity-budget-v0.json` and checksummed recovery evidence semantics;
- run `database/tests/run-source-validation.sh` and the approved disposable PostgreSQL integration gate.

Do not add a Senior 4 wrapper around the old `operations/**` paths.

## 4. Rebase and merge Senior 3

Senior 3 must publish:

- root Cargo workspace and locked toolchain;
- the exact runtime source/config/protocol contract commands in the provider registry;
- `contracts/platform/runtime-capacity-budget-v0.json` covering API, realtime and worker limits;
- telemetry implementation matching `telemetry-v0` and the runtime telemetry policy;
- a platform probe emitting `platform-probe-result-v0`, observing `LIQI_RELEASE_ID` and `LIQI_ENVIRONMENT`;
- health endpoints and systemd unit behavior consumed by activation.

Build commands remain owner-run under the project restriction. Senior 4 will integrate published commands, not infer them.

## 5. Run strict integration and promotion evidence

After all providers merge:

1. Run source CI; readiness must be `passed`.
2. Run the manual provider integration workflow against a disposable PostgreSQL target.
3. For promotion, supply the reviewed OCI plan artifact and checksummed Senior 2 recovery artifact by workflow run ID.
4. Require final `integration-result-v0=passed` before generating a deployment specification.
5. Run activation dry-run on the target host. Actual activation remains separately approved.

No step in this plan authorizes `tofu apply`, OCI mutation, deployment, migration or build execution.
