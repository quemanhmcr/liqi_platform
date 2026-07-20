# ADR 4006: First V1 production release without a fabricated application rollback target

- Status: Accepted
- Date: 2026-07-20
- Owner: Senior 4
- Consumers: Senior 1, Senior 2, Senior 5

## Context

The production host and retained fallback contain no installed V0 release, V0 service units, release manifests, health targets or database-compatibility evidence. A full predeploy boot-volume backup and a private stopped fallback do exist and have been observed directly. Treating either infrastructure resource as a V0 application release would create false evidence and unsafe rollback semantics.

## Decision

The first V1 release uses an explicit first-release transition:

- the signed Mix manifest contains `rollback_target_release_id: null`;
- migration compatibility is exactly 8/8/8 and down migration remains forbidden;
- `establish_first_release_recovery.py` produces `first-release-recovery-v1` evidence from live OCI after explicit approval, with OCIDs represented only by SHA-256 digests;
- pre-apply readiness requires that evidence bound to the exact source SHA;
- that evidence requires traffic-off state, an AVAILABLE full predeploy boot-volume backup, a private STOPPED fallback whose start/stop exercise restored its original state, and forward-only database recovery;
- first activation is valid only when `/opt/liqi/current` is absent and the target descriptor also declares no application rollback target;
- activation failure stops the new release and removes both current/runtime symlinks before any public traffic is enabled;
- an approved first-release recovery disables traffic before deactivation. Infrastructure restore is a separate explicit mutation.

After the first V1 release is healthy and retained, later releases use `release-switch` mode with a real previous V1 descriptor. The nullable first-release field is not a general waiver for upgrades.

## Alternatives considered

1. **Invent a V0 descriptor from the fallback instance.** Rejected because direct host inventory proved that no V0 workload exists.
2. **Use the new V1 release as its own rollback target.** Rejected because it provides no independent recovery boundary.
3. **Block production until a V0 artifact is rebuilt.** Rejected by the owner and unnecessary for correctness when traffic-off deactivation and infrastructure restore are explicit and tested.
4. **Proceed with no recovery gate.** Rejected because a single-node production release still requires a fail-closed recovery path.

## Trade-offs

First-release recovery has a longer RTO than an application release switch and may require a boot-volume restore. It avoids false assurances and reduces runtime complexity. Availability during host restore is not HA and must not be represented as such.
