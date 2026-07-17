# ADR 0412: Database recovery evidence in promotion

## Status

Accepted for V0.

## Decision

Senior 2 recovery status is a direct provider seam. Promotion downloads a protected artifact containing checksummed backup metadata, current backup/WAL status, the source metadata used for the restore proof and the isolated restore result. It invokes:

```text
bash database/bin/recovery-status.sh --environment <environment> --output <provider-output>
```

The command emits `recovery-status-v0` and validates against the Senior 4 schema when present. Provider JSON results receive a stable `gate_id`; `extract_provider_output.py` selects only a passed result under `.artifacts`. Senior 4 then runs freshness evaluation and composes final promotion evidence with provider, capacity and platform-probe results.

## Security and mutation

- The workflow reads evidence only.
- No backup, restore, migration, OCI or host mutation occurs.
- Environment path values are not rendered in integration command evidence.
- Checksums and semantic invariants remain Senior 2 responsibility.
- Provider logs remain redacted and separate from the provider JSON result.

## Compatibility

The change is additive. `database-recovery-signals` moves from pending to available. Promotion still cannot pass until Senior 1/3 capacity budgets, Senior 3 runtime gates/platform probe and cross-provider compatibility are satisfied.

## Separate unresolved boundary

The provider recovery-status seam is accepted. The restore engine location under `operations/**` remains a separate ownership conflict and is still rejected by ADR 0409/0410 compatibility policy.
