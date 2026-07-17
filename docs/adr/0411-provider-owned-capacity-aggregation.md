# ADR 0411: Provider-owned capacity aggregation

## Status

Accepted for V0.

## Decision

Production capacity aggregation consumes four provider-owned `capacity-budget-v0` documents. Test fixtures are never accepted by the default registry.

- Senior 1: infrastructure/edge/host budget — pending.
- Senior 2: `contracts/platform/database-capacity-budget-v0.json` — available.
- Senior 3: `contracts/platform/runtime-capacity-budget-v0.json` — available after commits `dd8d643`/`c8d9b96`.
- Senior 4: `operations/capacity/operations-capacity-budget-v0.json` — available.

`collect_provider_capacity.py` validates the registry, refuses missing or pending providers in strict mode, and delegates arithmetic and envelope enforcement to the canonical `check_capacity.py` aggregator. Source CI may emit a machine-readable `blocked` result while branches are unmerged; integration and promotion are strict.

ADR 0416 supersedes the original single CPU sum. The result now carries steady-state admission totals and truthful hard ceilings separately: steady CPU is capped at 3 OCPU, hard CPU ceilings at the physical 4 OCPU, hard memory at 20 GiB, hard disk at 180 GiB and PostgreSQL server reservation at 100.

## Consequences

- A provider without a hard budget cannot promote.
- Senior 4 does not infer runtime or infrastructure resource use from implementation files.
- Budget changes affecting shared headroom require consumer coordination and a decision note.
