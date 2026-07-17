# Runbook: activation failure V0

Owner: Senior 4. Provider failures remain owned by the provider named in the integration result.

1. Freeze further activation attempts and preserve the failed health-gate result.
2. Confirm the failure is readiness/platform-probe, not merely process liveness.
3. Select the manifest-declared rollback target with `scripts/release/select_rollback.py`.
4. Roll back application artifacts only; never run a database down migration.
5. Re-run the health gate against the retained target before declaring recovery.
6. Escalate to incident when rollback misses its deadline or fails.
7. Record any emergency manual action and reconcile it back into source after the incident.

Do not extend retry loops indefinitely and do not bypass fail-closed security checks to make readiness green.
