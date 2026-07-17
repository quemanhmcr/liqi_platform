# ADR 0411: Provider-owned capacity aggregation

## Status

Accepted for V0.

## Decision

Production capacity aggregation consumes four provider-owned `capacity-budget-v0` documents. Test fixtures are never accepted by the default registry.

- Senior 1: infrastructure/edge/host budget — pending.
- Senior 2: `contracts/platform/database-capacity-budget-v0.json` — available.
- Senior 3: API/realtime/worker budget — pending.
- Senior 4: `operations/capacity/operations-capacity-budget-v0.json` — available.

`collect_provider_capacity.py` validates the registry, refuses missing or pending providers in strict mode, and delegates arithmetic and envelope enforcement to the canonical `check_capacity.py` aggregator. Source CI may emit a machine-readable `blocked` result while branches are unmerged; integration and promotion are strict.

The result retains the V0 envelope: 4 OCPU/24 GiB host, 1 OCPU/4 GiB reserved, provider hard limits of 3 OCPU/20 GiB/180 GiB and 100 PostgreSQL connections.

## Consequences

- A provider without a hard budget cannot promote.
- Senior 4 does not infer runtime or infrastructure resource use from implementation files.
- Budget changes affecting shared headroom require consumer coordination and a decision note.
