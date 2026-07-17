# ADR 0100: OCI Host V0 Contract and Capacity Cost Classification

## Status

Accepted for V0 provider contract.

## Context

The project owner fixed the V0 host envelope as one `VM.Standard.A1.Flex` node with 4 OCPUs and 24 GB RAM. This is no longer a temporary sizing option. Oracle's current Always Free documentation states that an Always Free tenancy receives 1,500 A1 OCPU-hours and 9,000 GB-hours monthly, described as the continuous equivalent of 2 OCPUs and 12 GB RAM. OCI service limits and shape availability do not prove billing eligibility.

Consumers need a stable host contract now, while cost-sensitive infrastructure must fail closed.

## Decision

- Treat `free-tier-a1-4x24`, `VM.Standard.A1.Flex`, 4 OCPUs, and 24 GB RAM as the hard V0 host envelope.
- Classify the profile as `free-trial-only` until the owner verifies the tenancy billing entitlement in OCI.
- Require explicit non-Always-Free acknowledgement before OpenTofu may plan or apply the compute profile.
- Never infer cost safety from OCI service limits alone.
- Keep the module parameterized for a future PAYG or post-V0 migration, but reject any V0 plan that changes the fixed shape or 4/24 capacity.
- Use `liqi.platform.oci-host/v0` as the consumer contract schema version and `0.x.y` as additive infrastructure output versions.

## Contract semantics locked at Checkpoint 1

- Runtime services: `liqi-api`, `liqi-realtime`, `liqi-worker`.
- Runtime group: `liqi`; service users use stable names and numeric IDs.
- Release path: `/opt/liqi/releases`; active-release link: `/opt/liqi/current`.
- Runtime configuration: `/etc/liqi`.
- Secret materialization root: `/run/liqi/secrets`; outputs carry only OCI Vault references.
- PostgreSQL durable data and local backup staging live under `/var/lib/liqi/postgresql` on a separate block volume.
- Public ingress is edge-only on TCP 80/443; SSH is allowlisted and disabled by default; database, application, telemetry, metrics, and administration listeners are never public.
- Host readiness is an atomically written JSON artifact at `/run/liqi/host-ready.json` with schema `liqi.platform.host-readiness/v0`.
- Stateful block volume and Object Storage resources require explicit destruction acknowledgement.

## Consequences

- Senior 2, 3, and 4 can consume paths, identities, ports, storage references, readiness, and release target without inferring provider implementation details.
- A validation plan for 4/24 must include an explicit cost acknowledgement but still performs no OCI mutation.
- The profile name is stable for V0. Capacity is fixed independently from billing classification; `free-trial-only` remains authoritative until tenancy-specific evidence changes it.

## Affected workstreams

- Senior 2 consumes storage, PostgreSQL paths, Object Storage, and readiness semantics.
- Senior 3 consumes service identity, ports, configuration, and architecture semantics.
- Senior 4 consumes release target, output schema, readiness, and plan validation semantics.

## Removal condition

The `free-trial-only` classification may be changed only after the owner supplies tenancy-specific billing evidence showing that 4 OCPUs/24 GB is Always Free. The provider must update the contract example, cost guard test, and this ADR in the same commit.
