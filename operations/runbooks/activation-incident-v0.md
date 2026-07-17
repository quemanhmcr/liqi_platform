# Activation incident V0

1. Freeze further activation and migration commands.
2. Preserve the deployment spec, activation result, health results and journal window.
3. Confirm whether the current symlink points to the new or rollback release.
4. Confirm the three units independently; process-running is not readiness.
5. If the result is `rolled-back`, keep the previous release active and open a defect against the owning failed seam.
6. If the result is `incident`, keep writes stopped unless the incident commander explicitly authorizes a known-safe release.
7. Never run a database down migration.
8. Reconcile every manual action into source and the incident timeline.
