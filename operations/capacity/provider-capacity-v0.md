# Provider capacity V0

Run source mode:

```bash
python scripts/operations/collect_provider_capacity.py \
  --output .artifacts/provider-capacity-result.json \
  --allow-blocked
```

Omit `--allow-blocked` for integration and promotion. The command exits `2` when a provider budget is unavailable and `1` when a declared budget is invalid or exceeds the envelope.

The default registry cannot reference `tests/**`. Provider budgets must declare steady state, hard limit, PostgreSQL connections, bounded queue, bounded retry and failure behavior.
