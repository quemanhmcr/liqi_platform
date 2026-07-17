# ADR 0416: Separate steady-state admission from hard capacity ceilings

## Status

Accepted for V0 integration.

## Context

The V0 host is fixed at 4 OCPU, 24 GiB RAM and 200 GiB combined local storage. The initial Senior 4 checker summed every default-enabled hard CPU ceiling against a 3 OCPU planning limit. That conflated two different controls:

- steady-state admission, which must preserve host and recovery headroom;
- systemd/cgroup hard ceilings, which bound individual bursts but are not expected to peak simultaneously.

Senior 3 truthfully publishes a 0.8 OCPU runtime steady state and 1.45 OCPU runtime hard ceiling. Reducing the hard declaration to make the old sum pass would hide the real failure behavior. The first combined provider budgets also exposed a second modeling issue: runtime PgBouncer demand was added to database server reservation, double-counting the same pooled connections.

## Decision

1. The physical host remains exactly 4 OCPU and 24 GiB RAM. No PAYG or larger-host assumption is introduced.
2. Sum default-enabled `steady_state` resources separately from `hard_limit` resources.
3. Steady-state CPU admission must not exceed 3 OCPU. This preserves at least 1 OCPU of normal host/recovery scheduling headroom.
4. Aggregate hard CPU ceilings may total at most the physical 4 OCPU. They are burst ceilings, not a claim that every process can receive its maximum simultaneously.
5. Hard memory remains capped at 20 GiB and hard local disk at 180 GiB. Memory and disk are not safely overcommitted; 4 GiB RAM and 20 GiB disk remain reserved.
6. `capacity-result-v0` adds `steady_state_totals`, `hard_limit_totals` and `postgres_connection_accounting`. Legacy `totals` remains an exact alias of `hard_limit_totals` during V0 compatibility.
7. `integration-result-v0.capacity.declared_ocpu` remains the hard-ceiling aggregate and may be up to 4. New `steady_state_ocpu` and `steady_state_memory_mib` carry admission evidence.
8. PostgreSQL connections use two checks:
   - database components declare host server reservation, capped at 100;
   - non-database V0 consumers declare pooled server demand, which must not exceed the PgBouncer server capacity.
   Runtime demand is not added again to the database reservation.
9. Recovery remains a default platform capability. Its hard ceiling may overlap normal runtime in time, but operators must treat simultaneous CPU saturation as bounded degradation and prioritize the host/recovery slice. No swap or permissive fallback is allowed.

## Verified combined fixture

Using the published Senior 2 and Senior 3 budgets plus current edge/operations fixtures:

| Dimension | Steady state | Hard ceiling / reservation | Limit |
|---|---:|---:|---:|
| CPU | 1.66 OCPU | 3.30 OCPU | 3 steady / 4 hard |
| Memory | 6528 MiB | 16384 MiB | 20480 MiB hard |
| Local disk | 67.6 GiB | 160.2 GiB | 180 GiB hard |
| PostgreSQL | 35 pooled demand | 82 server reservation | 40 pooled / 100 server |

The fixture is evidence of accounting semantics, not a substitute for Senior 1's provider-owned capacity contract.

## Trade-offs

- A simultaneous hard CPU peak can saturate the host. V0 accepts bounded CPU contention rather than publishing false limits; readiness, queue rejection and systemd scheduling controls must degrade safely.
- The connection model is intentionally V0-specific to one PgBouncer transaction-pooling boundary. Split-node or multiple-pool architectures require a versioned accounting contract.
- Memory and disk remain strict sums because overcommit can trigger OOM or recovery failure.

## Compatibility

The result contract change is additive. `totals` remains available with its prior hard-ceiling meaning. Consumers that enforce host reserve must use `steady_state_totals`; consumers that configure systemd/cgroup ceilings use `hard_limit_totals`. No provider implementation is rewritten by Senior 4.
