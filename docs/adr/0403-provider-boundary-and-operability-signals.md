# ADR 0403: Provider boundary and operability signals

- Status: Proposed coordination change
- Owner: Senior 4
- Date: 2026-07-17

## Assumptions no longer valid

The Senior 2 database contract currently names restore commands under `operations/disaster-recovery/database/**`. Senior 4 owns orchestration and evidence freshness, but explicitly does not own the database restore engine. Placing implementation commands under Senior 4 ownership would create an integration sink.

Senior 1 and Senior 2 validators currently provide stable exit codes and human output but do not yet emit all machine-readable capacity/recovery signals required by the operations contracts.

## Boundary change

- Database backup/restore implementation commands remain under `database/**` and are owned by Senior 2.
- `operations/**` invokes those commands, evaluates freshness and orchestrates exercises; it does not implement PostgreSQL restore mechanics.
- Senior 1, Senior 2 and Senior 3 each publish `capacity-budget-v0` declarations.
- Senior 2 publishes `recovery-status-v0` evidence after backup/WAL/restore verification.

## Affected work

Senior 2 must update command paths or provide a Senior 2-owned compatibility adapter during the migration window. Senior 1/2/3 must emit their required capacity and telemetry/recovery declarations. Senior 4 updates only invocation registry and integration gates.

## Trade-off and compatibility

This adds a small provider adapter/output burden but preserves ownership, keeps provider failures attributable and prevents operational orchestration from becoming a database implementation layer. Existing command strings remain accepted only until checkpoint 2; removal owner is Senior 2.
