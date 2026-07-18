# V1 provider integration contract

Senior 5 consumes provider commands from `operations/readiness/provider-gates-v1.json`. The registry is descriptive and fail-closed; it does not implement provider behavior.

## Publication rule

A provider changes its entry from `pending-provider-publication` to `available` only when the exact command, required paths and output contract exist on the integrated branch. The publishing commit must include the required communication footer and provider validation command.

Provider output must be bound to the exact Git SHA and release ID whenever the result schema carries those fields. Live evidence must identify `evidence_mode: live`; fixtures and synthetic evidence are test-only.

## Owner actions

- Senior 1: publish BEAM source/integration/artifact commands and `live-platform-probe-v1` output.
- Senior 2: publish database source/integration commands and approved isolated `recovery-result-v1` output.
- Senior 3: publish native source/safety/artifact commands with benchmark, fuzz/property, fallback and ARM64 identity evidence.
- Senior 4: publish infrastructure source/plan, host readiness and `rollback-result-v1` evidence. OCI and traffic mutation remain Senior 4-only and require explicit approval.

## Blocked seam format

```text
Blocked seam:
Provider: Senior <n>
Consumer: Senior 5
Missing contract: <exact command/output/path>
Why current work cannot safely continue: <semantic or evidence gap>
Minimal provider output required: <directly consumable result>
Temporary work that remains independent: readiness schemas, validators, load/resilience harness and runbooks
```

No readiness-owned provider wrapper, permissive fallback or fixture is accepted as production evidence.
