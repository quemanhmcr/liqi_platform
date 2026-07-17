# ADR 0401: Deterministic release identity and capacity envelope

- Status: Accepted for V0
- Owner: Senior 4
- Date: 2026-07-17

## Decision

Release manifests are generated from immutable source metadata and file digests; wall-clock generation time is excluded. Artifact ordering and JSON key ordering are canonical. Build provenance and SBOM references are mandatory before staging promotion.

Every provider emits a `capacity-budget-v0` declaration. Integration sums only default-enabled hard limits and fails when any provider budget is absent or when totals exceed 3 OCPU, 20 GiB RAM, 180 GiB disk or 100 PostgreSQL server connections. The remaining 1 OCPU, 4 GiB RAM and 20 GiB disk are reserved for the OS, recovery and incident headroom.

## Consequences

- Free Tier capacity is an enforced contract rather than a planning note.
- A new component cannot silently consume headroom.
- Swap is not counted as capacity.
- On-volume backup bytes are not counted as recovery evidence.
- Mock artifacts may exercise deterministic generation but cannot satisfy a promotion gate.
