# Source integration readiness V0

`source-integration-readiness-v0.json` is the single machine-readable summary for pull requests and `main` source state. It composes three independent evidence files:

1. published provider source commands;
2. cross-provider compatibility;
3. provider-owned capacity budgets.

Status semantics:

- `passed`: every checkpoint passed and no blockers remain;
- `blocked`: one or more provider seams are not merged yet, but no present seam violates its contract;
- `failed`: a present provider command, shared semantic or capacity envelope is invalid.

Source CI permits `blocked` during the V0 branch integration window. It does not permit `failed`. Evidence-producing steps use `continue-on-error` only so the report and logs can still be uploaded; the readiness assembler becomes the final job result.

Every blocker names an owner, seam, stable code, severity, message and required action. Senior 4 does not add fallback implementation for a blocked or failed provider.
