# ADR 0400: Operability control-plane contracts

- Status: Accepted for V0 checkpoint 1
- Owner: Senior 4
- Date: 2026-07-17

## Context

LIQI Platform V0 has four independent providers and one single-node capacity envelope. Integration must fail at provider seams rather than accumulating glue in a final branch. Deployment is a health-gated replacement/restart, not a claim of zero-downtime canary or high availability.

## Decision

The control plane uses three initial, machine-readable Draft 2020-12 JSON contracts:

1. `release-manifest-v0` traces a release to source, artifacts, contract versions, migration range, infrastructure output, runtime configuration, SBOM/provenance and a preselected rollback target.
2. `telemetry-v0` is a provider capability declaration for health, logs, metrics, traces, cardinality and redaction. Senior 3 implements application instrumentation; Senior 4 owns the required semantics.
3. `integration-result-v0` records provider commands, integration gates, capacity, recovery freshness, platform probe and owner-addressed violations.

Mock provider fixtures are allowed only for checkpoint-1 pipeline design. A production or promotion gate must use `mode=provider` and must fail when provider commands or evidence are absent.

## Consequences

- Providers can implement independently against explicit operations seams.
- A running process is not readiness; readiness and platform probe remain separate.
- A backup record without restore evidence cannot satisfy recovery readiness.
- Release generation must be deterministic from Git/source timestamps and artifact digests.
- Database rollback remains prohibited; application rollback relies on forward-compatible migrations.
- Cost classification `paid` or `unknown` cannot be activated by default.

## Rejected alternatives

- Prose-only requirements: not mechanically enforceable.
- A generic integration wrapper that compensates for provider failures: creates an integration sink and duplicate semantics.
- A single schema for all provider internals: over-couples implementation details and violates DRI boundaries.
