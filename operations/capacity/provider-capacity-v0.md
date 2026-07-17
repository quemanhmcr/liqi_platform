# Provider capacity V0

Run source mode:

```bash
python scripts/operations/collect_provider_capacity.py \
  --output .artifacts/provider-capacity-result.json \
  --allow-blocked
```

Omit `--allow-blocked` for integration and promotion. The command exits `2` when a provider budget is unavailable and `1` when a declared budget is invalid or exceeds the envelope.

The default registry cannot reference `tests/**`. Provider budgets must declare steady state, hard limit, PostgreSQL connections, bounded queue, bounded retry and failure behavior.

Senior 3's validation manifest CPU and memory hints are not a capacity budget. Runtime readiness requires a provider-owned `capacity-budget-v0` document covering hard OCPU, memory, disk, PostgreSQL connections, queue/retry bounds and failure behavior for all three processes.
