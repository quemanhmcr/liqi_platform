# ADR 0410: Provider validation and protected OCI plan evidence

## Status

Accepted for V0.

## Decision

Senior 4 invokes the exact Senior 1 commands published in commit `b1c3d59`:

- `python infrastructure/validation/validate_infrastructure.py --with-tofu` for source, cost, security, output, bootstrap and OpenTofu validation;
- `python infrastructure/validation/validate_oci_plan.py <plan.json>` for promotion plan evidence.

CI pins OpenTofu 1.12.1 and `opentofu/setup-opentofu` by immutable commit. The normal source workflow performs no plan or OCI authentication. Promotion downloads a reviewed artifact containing exactly one `oci-plan.json`; the artifact run ID is an explicit manual input. The provider runner expands the path through `{env:LIQI_OCI_PLAN_JSON}` while command evidence records only the environment variable name, never its value.

A Senior 4 cross-provider compatibility gate checks only shared seams:

- OCI host output/release paths against deployment contracts;
- host journald retention against the Senior 4 telemetry policy;
- database recovery command ownership against repository boundaries.

It does not reproduce OpenTofu, cloud-init, PostgreSQL or restore implementation checks.

## Current provider actions

- Senior 1 cloud-init currently declares `SystemKeepFree=7G`; the V0 operations policy requires 10 GiB. Senior 1 must align the host source or publish a coordinated contract change.
- Senior 2 currently names restore commands under `operations/**`; restore implementation must be provider-owned under `database/**` or versioned with a provider-owned compatibility path.

## Gate rollout

Missing provider branches may report `blocked` in source CI during checkpoint integration. Once the relevant provider paths exist, a semantic mismatch fails immediately. There is no grace period for security, recovery ownership, cost classification or log/disk headroom violations.

## Non-goals

This workflow does not create a plan, run `tofu apply`, authenticate to OCI, activate a host, run migrations or repair provider source.
