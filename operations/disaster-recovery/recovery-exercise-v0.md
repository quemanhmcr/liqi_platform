# Recovery exercise V0

The runner orchestrates Senior 2 commands; it does not implement backup or restore.

- Default is dry-run.
- Provider mode accepts commands only under `database/**`.
- Execution requires the exact approval reference recorded in the plan.
- The target is isolated, receives no production traffic and cannot mutate the source database or OCI.
- Every command has a deadline and redacted log evidence.
- Provider verification must emit `recovery-status-v0`; Senior 4 then applies recovery freshness policy.
- Cleanup is always attempted. Cleanup failure is an incident.

The current Senior 2 draft points restore commands into `operations/**`; this violates ownership and remains blocked until Senior 2 moves or versions that seam.
