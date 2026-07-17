# ADR 0103: V0 Capacity Aggregation and systemd Enforcement

## Status

Accepted for the fixed V0 host envelope.

## Context

The project owner fixed V0 at one OCI `VM.Standard.A1.Flex` host with 4 OCPUs and 24 GiB RAM. The host must always retain at least 1 OCPU and 4 GiB RAM for the operating system, recovery, and incident spikes. Provider processes therefore share an allocatable envelope of 3 OCPUs and 20 GiB RAM.

Provider contracts expose both steady-state planning demand and per-component hard ceilings. CPU is compressible and scheduled; memory is not safely overcommitted in V0 because swap is disabled. Runtime truthfully declares 1.45 OCPUs of hard ceilings and must not be reduced merely to satisfy aggregation. Senior 2 subsequently removed duplicate database capacity semantics in commit `3736a57`; with current provider contracts, enabled hard ceilings total exactly 3.00 OCPUs and expected steady-state demand totals 1.51 OCPUs.

PostgreSQL connection fields describe different layers: runtime declares 35 logical client connections, PgBouncer declares 5 backend PostgreSQL connections, PostgreSQL authority declares 8 normal server connections, and recovery reserves 2. These values must not be interpreted as one homogeneous demand total.

## Decision

The fixed host envelope is authoritative:

- Host: 4 OCPUs, 24 GiB RAM.
- Host scheduling reserve: 1 OCPU.
- Host memory reserve: 4 GiB.
- Provider parent ceiling: 3 OCPUs and 20 GiB RAM.
- Swap is disabled and `MemorySwapMax=0` is applied to LIQI slices.

Capacity aggregation uses separate semantics:

1. **Steady-state planning budget**
   - Sum enabled components' `steady_state.ocpu`.
   - The result must not exceed 3 OCPUs.
   - Use this value for admission, normal capacity planning, and identifying sustained saturation.

2. **Per-component hard CPU ceiling**
   - Preserve each provider's declared `hard_limit.ocpu` exactly.
   - Do not reduce truthful ceilings merely to make an additive checker pass.
   - V0 uses a conservative additive admission rule: enabled provider hard ceilings must total no more than 3 OCPUs.
   - The aggregate parent slice independently enforces the same 3 OCPU ceiling at runtime.

3. **Hard memory and disk limits**
   - Sum enabled components' hard memory and disk limits.
   - Hard memory must not exceed 20 GiB.
   - Hard disk must fit the provider disk envelope after the host recovery reserve.
   - Memory hard limits are not overcommitted in V0.

4. **PostgreSQL connections**
   - Runtime client demand is 35 logical client connections.
   - PgBouncer backend connections and PostgreSQL server/recovery slots are separate capacity layers.
   - Validate each boundary using the database provider contract and pool semantics; do not treat all fields as interchangeable connections.

## systemd hierarchy

The host provider publishes and materializes:

- `liqi-platform.slice`: parent ceiling `CPUQuota=300%`, `MemoryMax=20G`, `MemorySwapMax=0`.
- `liqi-platform-runtime.slice`: runtime ceiling `CPUQuota=145%`, `MemoryMax=7G`, `MemorySwapMax=0`.
- `liqi-platform-database.slice`: database ceiling `CPUQuota=120%`, `MemoryMax=7936M`, `MemorySwapMax=0`.
- `liqi-platform-operations.slice`: operations ceiling `CPUQuota=25%`, `MemoryMax=1G`, `MemorySwapMax=0`.
- `liqi-platform-edge.slice`: edge ceiling `CPUQuota=10%`, `MemoryMax=256M`, `MemorySwapMax=0`.
- Runtime service drop-ins preserving Senior 3 hard limits:
  - `liqi-api.service`: 45%, 2 GiB.
  - `liqi-realtime.service`: 65%, 3 GiB.
  - `liqi-worker.service`: 35%, 2 GiB.

A container runtime may apply an equal or lower inner limit, but may never exceed or replace the systemd outer authority. Base service units remain owned by their provider/release DRI; Senior 1 owns host-level slices and capacity drop-ins.

## Host contract

Infrastructure output `0.3.0` adds:

- Exact runtime configuration paths and `LIQI_CONFIG_PATH` handoff.
- systemd slice names and parent/child relationships.
- Per-service CPU and memory controls.
- CPU aggregation and memory aggregation policy.
- Fixed 4 OCPU/24 GiB envelope and 1 OCPU/4 GiB reserve.

The additions are backward-compatible under `liqi.platform.oci-host/v0`. Existing `0.2.x` consumers may ignore the new fields for one integration window.

## Consumer action

- Senior 2 must attach database units to a child of `liqi-platform.slice` and preserve its own budget semantics.
- Senior 3 may consume `/etc/liqi/{api,realtime,worker}.json`; no runtime budget changes are required.
- Senior 4 may retain the conservative hard-CPU sum gate because current truthful ceilings total exactly 3 OCPUs, but must report steady-state CPU separately and must not treat PostgreSQL client demand, backend pools, and server slots as one homogeneous total.

## Trade-offs

- Current enabled hard CPU ceilings sum exactly to the 3 OCPU parent cap; any provider increase requires a coordinated capacity decision.
- The explicit parent reserve prevents LIQI workloads from consuming the fourth OCPU during incidents.
- Disabling swap makes memory exhaustion visible and bounded but requires correct `MemoryMax` values and restart/incident handling.

## Compatibility and removal

This is an additive V0 contract change. Existing `0.2.x` consumers remain valid for one integration window. No compatibility implementation may lower a truthful provider hard limit; the migration is complete when Senior 4 consumes output `0.3.0`, marks the infrastructure budget available, and reports steady-state CPU and connection layers explicitly.
