# ADR 0304: Runtime operability provider contracts V0

## Status

Accepted for Senior 3 provider publication.

## Context

Senior 4 integration commit `06ebf8e` consumes the Rust runtime foundation from `7ed9cc9` and requires provider-owned capacity, telemetry and platform-probe evidence. Senior 3 must publish truthful capabilities rather than declarations that merely satisfy JSON Schema.

Senior 1's coordination ADR assigns runtime ceilings of 0.45/2 GiB for API, 0.65/3 GiB for realtime and 0.35/2 GiB for worker. Runtime configuration bounds PostgreSQL pools to 20/5/10, API admission to 256, realtime outbound queues to 128 and worker claims to 32 with eight attempts.

The current cross-provider OCPU aggregation is internally inconsistent: Senior 1's table totals 3.50 OCPU for consumers, while Senior 4's `capacity-envelope-v0` aggregates provider hard limits against 3.0 OCPU. Database and operations budgets already consume 1.75 OCPU, leaving 1.25 OCPU for runtime plus infrastructure, while the Senior 1 runtime ceiling alone is 1.45 OCPU. Senior 3 must not hide this by publishing unrealistically low limits.

## Decision

1. Publish `runtime-capacity-budget-v0.json` using Senior 1's runtime CPU/memory ceilings and the actual configured pool/queue/retry bounds.
2. Allocate 12 GiB total hard disk to runtime artifacts and transient runtime state, below the 14 GiB release/image planning budget.
3. Publish one telemetry declaration per binary. Declarations list only signals implemented by Senior 3 and use bounded labels, W3C propagation, parent-based sampling and fail-closed redaction.
4. Every runtime trace resource carries `service.namespace`, `service.name`, `service.version`, `deployment.environment.name` and `liqi.release.id`; V0 sets `liqi.release.id` equal to the deployed `service.version`.
5. Add `/health/platform` as a machine-readable application-layer dependency health alias. Promotion evidence remains the separate provider-owned platform probe result; this endpoint does not claim end-to-end success.
6. The provider-owned platform probe must fail when committed realtime delivery cannot be observed. It must not read authority tables using the realtime role or synthesize a delivery event.

## Cross-provider action required

Senior 1, Senior 2 and Senior 4 must reconcile the OCPU aggregation model before the combined capacity gate can pass. Acceptable options are:

- aggregate steady-state OCPU while retaining memory/disk hard ceilings;
- raise the aggregate OCPU scheduling envelope consistently with Senior 1's 3.50 OCPU consumer plan; or
- disable a non-steady recovery component by default and publish an explicit recovery-mode capacity profile.

Changing Senior 3 limits below the runtime contract is not an acceptable mitigation.

## Compatibility

All wire and health changes are additive. `service.version` remains the health release identity. Metadata adds explicit `releaseId` and `environment` fields. Existing V0 error, event and realtime versions do not change.
