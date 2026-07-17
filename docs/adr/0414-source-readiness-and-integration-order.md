# ADR 0414: Source readiness and integration order

## Status

Accepted for V0.

## Decision

Create `integration-readiness-result-v0` as the source-level integration authority for operations. It composes provider source, compatibility and capacity evidence without duplicating provider logic.

Source CI preserves failed provider evidence through `continue-on-error`, then makes the composed readiness result authoritative:

- absent provider seams are `blocked` and permitted during the branch integration window;
- present invalid seams are `failed` and fail CI;
- all checkpoints must pass before strict integration or promotion.

Merge Senior 4 first, then rebase/fix Senior 1, Senior 2 and Senior 3 in that order. The ordering follows single-writer ownership and lets providers implement against stable operations contracts. It is not an integration branch where Senior 4 repairs provider internals.

## Current provider corrections

- Senior 1: publish capacity budget and align journald policy.
- Senior 2: remove restore implementation/runbooks from Senior 4-owned `operations/**` and publish provider-owned recovery exercise commands.
- Senior 3: publish all runtime commands, capacity, telemetry and platform probe seams.

## Compatibility

The readiness result is additive. Removal of the temporary `blocked` allowance requires all three provider branches merged and a decision note changing source CI policy.
