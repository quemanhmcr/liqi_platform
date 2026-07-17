# Golden deployment path V0

The following artifacts are immutable inputs to activation:

```text
release-manifest-v0
provider-mode integration-result-v0 (passed)
OCI host output
health-gate-target-v0
→ deployment-spec-v0
```

`generate_deployment_spec.py` is preflight and planning only. It performs no upload, SSH, systemd action, firewall change, migration or OCI mutation.

Activation must later follow the specification exactly:

1. verify artifact, SBOM and provenance digests;
2. verify host readiness and required database migration range;
3. retain and preselect the previous compatible release;
4. stage files outside the active release directory;
5. install root-owned immutable release content;
6. stop/start services with bounded deadlines;
7. run liveness, readiness and platform probe;
8. atomically select the new release only after health passes;
9. on failure, stop the new release and select the predeclared previous release;
10. if rollback fails or no previous release exists, enter incident state.

Single-node V0 uses health-gated replacement/restart. It must never be described as canary, HA or zero downtime.
