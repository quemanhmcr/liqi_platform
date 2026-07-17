# Deployment and rollback V0

V0 uses single-node health-gated replacement/restart. It is not described as HA, zero-downtime or canary.

## Activation

1. Validate clean source and operations contracts.
2. Validate provider commands in provider mode.
3. Generate and validate the deterministic release manifest.
4. Verify cost classification, migration compatibility, runtime config and preselected rollback target.
5. Stage immutable artifacts without switching the active release.
6. Stop accepting new work where the provider supports drain; respect bounded shutdown deadlines.
7. Activate the staged release specification.
8. Require liveness, readiness and platform probe before marking active.
9. Record release ID in telemetry and deployment history.

No OCI apply or production activation is automatic by default.

## Failed activation

- Stop retrying when the health deadline expires.
- Mark activation `failed`.
- Invoke only the preselected retained application rollback target.
- Do not run a database down migration.
- Run the same three-part health gate against the rollback release.
- Mark `rolled-back` only after the gate passes.
- Mark `incident` when rollback is missing, incompatible, exceeds its deadline or fails health verification.

## Retention

Retain at least the active release and one rollback-compatible predecessor, including manifest, artifacts, checksums, SBOM and provenance. Removal requires proof that no active environment references the release.
