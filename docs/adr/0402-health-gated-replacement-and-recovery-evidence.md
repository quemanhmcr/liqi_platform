# ADR 0402: Health-gated replacement and recovery evidence

- Status: Accepted for V0
- Owner: Senior 4
- Date: 2026-07-17

## Decision

Single-node V0 deployment is a bounded health-gated replacement/restart. Liveness, readiness and platform probe are distinct required checks. Activation failure uses a predeclared retained application release and never executes database down migration.

Senior 2 exposes recovery evidence through `recovery-status-v0`. Promotion requires fresh encrypted off-host backup evidence, bounded WAL archive lag and a successful restore verification within policy age and RPO/RTO targets.

## Consequences

- Process-running cannot satisfy readiness.
- Backup-created cannot satisfy recovery readiness.
- Failed rollback becomes explicit incident state.
- HTTPS is mandatory for staging/production health targets.
- This design can later place the same gate around multi-node canary without changing release semantics.
