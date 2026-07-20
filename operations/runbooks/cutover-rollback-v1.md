# Traffic cutover and first-release recovery V1

## Trigger

- A phased cutover or release activation gate fails.
- A correctness, security, SLO, host or storage condition requires removal of public traffic.
- The first V1 release must be deactivated or the predeploy boot-volume restore procedure must be invoked.

## Safety model

The first production V1 release has no previous application release. No target is invented. Public traffic remains disabled until private application health and NLB backend health pass. Database migration 8 is forward-only; recovery never runs a down migration.

The proven recovery boundary is:

1. Disable public traffic or mark both NLB backends offline before stopping V1.
2. Stop new admission, drain bounded sessions and stop the V1 services.
3. Remove `/opt/liqi/current` and `/etc/liqi/runtime/current.json` when deactivating the first release.
4. For host corruption, restore only from the exact AVAILABLE full predeploy boot-volume backup bound in `first-release-recovery-v1` evidence, or start the private stopped fallback after an approved incident decision.
5. Re-run private health, database readiness and security checks before any later traffic enablement.

Future V1-to-V1 upgrades use the same controller in `release-switch` mode with a real retained V1 descriptor.

## Operator procedure

1. Record exact Git SHA, release ID, NLB/backend state, current symlink target, database migration readiness and the approval reference.
2. Disable traffic first and verify external probes no longer reach the backend.
3. Run `release_control.py rollback` with `--first-release-recovery` for first-release deactivation. Do not pass a target release ID.
4. Verify V1 services are stopped, current/runtime symlinks are absent, the fallback remains private, and no public IP exists on either instance.
5. Restore a boot volume only when deactivation is insufficient and the owner approves the infrastructure mutation.
6. Retain command results, OCI mutation evidence, health output and UTC timestamps. Never retain secret values or private key material.

## Acceptance

A recovery exercise passes only when traffic is fail-closed, all deactivation steps pass, no database down migration occurs, durable-event loss is zero, and the exact fallback/backup evidence is still valid. Missing, stale, synthetic, blocked or SHA-mismatched evidence is not a pass.
