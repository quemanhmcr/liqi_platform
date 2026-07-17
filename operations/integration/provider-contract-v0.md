# Provider command contract V0

Senior 4 invokes provider commands and does not reproduce provider logic.

Each provider command must:

1. Be read-only unless its command name and runbook explicitly identify an owner-approved deployment/recovery action.
2. Exit `0` only when the provider contract is satisfied.
3. Write a machine-readable JSON result to an explicit output path supplied by Senior 4.
4. Write human diagnostics to stderr without secrets.
5. Be deterministic for the same source/configuration inputs.
6. Identify contract version, owner, Git SHA, command version and evidence references.
7. Declare capacity and failure behavior where applicable.

Expected checkpoint-2 seams:

| Owner | Required seam | Senior 4 behavior when absent |
|---|---|---|
| Senior 1 | OCI plan/host contract validation and cost classification | `blocked`, owner Senior 1 |
| Senior 2 | migration/database readiness plus backup and restore freshness | `blocked`, owner Senior 2 |
| Senior 3 | runtime source/protocol/config/telemetry contract validation and platform probe | `blocked`, owner Senior 3 |

Mock commands are restricted to checkpoint-1 contract tests. Promotion and production gates reject `mode=mock`.
