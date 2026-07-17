# ADR 0409: Provider-owned recovery exercise orchestration

## Status

Accepted for V0.

## Decision

Senior 4 owns a bounded recovery exercise state machine and evidence format. Senior 2 owns every database prepare, restore, verify and cleanup command. Provider commands execute as argv arrays without a shell, have explicit deadlines, write redacted evidence, target an isolated data directory/database and never mutate the source database, production traffic or OCI.

The exact lifecycle is `prepare → restore → verify → cleanup`. Cleanup is always attempted. Provider verification must emit `recovery-status-v0`; Senior 4 evaluates that output against backup, WAL, restore freshness, RPO and RTO policy.

Dry-run is the default. Execution requires the exact approval reference in the plan. Mock commands are test-only and require an explicit flag.

## Boundary correction

The current Senior 2 draft names restore commands under `operations/disaster-recovery/database/**`. That assumption conflicts with repository ownership: `operations/**` is Senior 4 while the restore engine belongs to Senior 2. Provider-mode recovery therefore accepts commands only under `database/**` and remains blocked until Senior 2 versions or moves the seam. Senior 4 will not add a wrapper that conceals the conflict.

## Consequences

- Backup creation alone can never satisfy recovery readiness.
- Cleanup failure is an incident because isolated state may consume capacity or be unsafe to reuse.
- Exercise evidence is machine-readable and can feed promotion freshness gates.
- No live recovery exercise has been run by this task.
## V0 closeout resolution

Senior 2 now publishes the complete `prepare → restore → verify → cleanup` lifecycle under `database/recovery/**`. Provider-mode dry-run resolves every command directly; no compatibility wrapper exists under `operations/**`.
