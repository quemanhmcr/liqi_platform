# Provider capacity V0

Run source mode:

```bash
python scripts/operations/collect_provider_capacity.py \
  --output .artifacts/provider-capacity-result.json \
  --allow-blocked
```

Omit `--allow-blocked` for integration and promotion. The command exits `2` when a provider budget is unavailable and `1` when a declared budget is invalid or exceeds the envelope.

The default registry cannot reference `tests/**`. Provider budgets must declare steady state, hard limit, PostgreSQL connections, bounded queue, bounded retry and failure behavior.

Senior 3 publishes `contracts/platform/runtime-capacity-budget-v0.json`; the default registry treats it as available once merged. Capacity output separates `steady_state_totals` from `hard_limit_totals`. Legacy `totals` remains an alias of hard ceilings. Steady CPU admission is capped at 3 OCPU, while aggregate hard CPU ceilings are host-capped at 4 OCPU. PostgreSQL accounting compares pooled runtime demand with PgBouncer server capacity and does not double-count that demand in the database server reservation.
