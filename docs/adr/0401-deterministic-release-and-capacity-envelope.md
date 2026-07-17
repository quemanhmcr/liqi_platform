# ADR 0401: Deterministic release identity and capacity envelope

- Status: Accepted for V0
- Owner: Senior 4
- Date: 2026-07-17

## Decision

Release manifests are generated from immutable source metadata and file digests; wall-clock generation time is excluded. Artifact ordering and JSON key ordering are canonical. Build provenance and SBOM references are mandatory before staging promotion.

Every provider emits a `capacity-budget-v0` declaration. ADR 0416 refines CPU accounting: default-enabled steady-state CPU is admitted against 3 OCPU, while truthful process hard CPU ceilings are host-capped at 4 OCPU. Hard memory remains capped at 20 GiB, hard local disk at 180 GiB and PostgreSQL server reservation at 100. The remaining 1 OCPU steady-state admission, 4 GiB RAM and 20 GiB disk protect the OS, recovery and incident path.

## Consequences

- Free Tier capacity is an enforced contract rather than a planning note.
- A new component cannot silently consume headroom.
- Swap is not counted as capacity.
- On-volume backup bytes are not counted as recovery evidence.
- Mock artifacts may exercise deterministic generation but cannot satisfy a promotion gate.
