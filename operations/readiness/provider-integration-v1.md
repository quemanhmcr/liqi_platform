# V1 provider integration contract

Senior 5 consumes provider commands from `operations/readiness/provider-gates-v1.json`. The registry is descriptive and fail-closed; it does not implement Phoenix, PostgreSQL, native or OCI behavior.

## Provider states

- `pending-provider-publication`: the owned consumer-ready command or output does not exist. `provider_commit` is null.
- `pending-integration`: the command exists at a recorded provider commit but is not yet in the integrated graph.
- `available`: the exact command and required paths are in the integrated graph and provider/consumer source validation passed. The provider commit is retained for provenance.
- `pending-live-evidence`: the command or verifier is integrated, but the exact-release artifact/live result is absent.

None of the pending states can produce a passed checkpoint.

## Integrated graph

The current committed provider ancestry is:

```text
Senior 1 runtime:        15e2dd5a263decb91308a0d1783c4610bd7dc62d
Senior 2 database:       168f6b3be66ff36eac4b4944f8d6940b6d2026ce
Senior 3 native:         7478e31a4de48e278f0d08885bfaab56d5d88762
Senior 4 infrastructure: 19b06788e0a5d7695fc2f89102af8e75129d39af
```

All four commits are ancestors of `v1/production-readiness`. Exact provider evidence must still be generated on the final composite SHA; evidence from a provider-only or earlier integration SHA is supporting material, not a final checkpoint pass.

## Current gate classification

Source gates for runtime, database, native and infrastructure are `available`. The Senior 1 disposable PostgreSQL composite integration gate is also `available` and includes an explicit database-provider integration check.

Artifact and live collectors remain `pending-live-evidence`. The standalone Senior 2 disposable database evidence command, Senior 2 isolated restore wrapper and Senior 3 full safety command remain `pending-provider-publication` because no directly consumable owner output exists at those seams.

## Evidence and mutation rule

Provider output must be bound to the exact Git SHA and release ID whenever its schema carries those fields. Live evidence must identify live execution; examples, fixtures, synthetic results and local non-promotable packages are test-only.

Source validation, local builds, disposable databases and read-only plan inspection do not authorize OCI mutation. OCI apply, secret/IAM changes, live migration, deployment, traffic switching, restore and rollback execution require explicit approval and the owning Senior 4/Senior 2 executor.

The unresolved seams and removal conditions are listed in `operations/readiness/blocked-provider-seams-v1.md`.
