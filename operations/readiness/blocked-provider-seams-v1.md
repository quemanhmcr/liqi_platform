# V1 provider integration and remaining evidence seams

The committed provider graph is integrated on `v1/production-readiness`. Source publication is no longer blocked for Senior 1, Senior 2, Senior 3 or Senior 4. The registry remains fail-closed for commands or live results that are still absent.

## Integrated provider commits

| Provider | Exact provider commit | Integrated capability | Current evidence state |
|---|---|---|---|
| Senior 1 | `15e2dd5a263decb91308a0d1783c4610bd7dc62d` | BEAM source gate, disposable PostgreSQL runtime integration, release verifier and live platform probe | Source and disposable integration published; Linux ARM64 artifact and live endpoint evidence pending. |
| Senior 2 | `168f6b3be66ff36eac4b4944f8d6940b6d2026ce` | PostgreSQL V1 authority, migrations 5–8, outbox, Oban, readiness and recovery status | Source contracts integrated; a standalone CI-consumable database integration result and approved isolated restore result remain unpublished. |
| Senior 3 | `7478e31a4de48e278f0d08885bfaab56d5d88762` | Bounded sequence-diff core/NIF, Rust source gate and artifact verifier | Rust source passes; full Linux ARM64 Rustler, fuzz/A1 scheduler/latency and signed artifact evidence pending. |
| Senior 4 | `19b06788e0a5d7695fc2f89102af8e75129d39af` | OCI source/contracts, deterministic host bundle, plan/apply controls, release staging and rollback controller | Source passes without mutation; reviewed live plan, deployed host, activation, edge and rollback evidence pending. |

## Remaining owner seams

```text
Blocked seam:
Provider: Senior 2
Consumer: Senior 5
Missing contract: one CI-consumable disposable PostgreSQL integration command with exact-SHA machine-readable output
Why current work cannot safely continue: the composite Senior 1 integration proves consumption, but it does not replace an independently attributable Senior 2 provider result.
Minimal provider output required: database-integration result registered as available
Temporary work that remains independent: source checkpoint and runtime composite integration evidence
```

```text
Blocked seam:
Provider: Senior 2
Consumer: Senior 5
Missing contract: approved isolated restore/PITR wrapper producing recovery-result-v1
Why current work cannot safely continue: backup freshness and recovery status do not prove restore, migration/invariant checks, read-only probe or cleanup.
Minimal provider output required: exact-release isolated restore evidence with approval reference
Temporary work that remains independent: recovery schema, validator and runbook
```

```text
Blocked seam:
Provider: Senior 3
Consumer: Senior 5
Missing contract: one command producing full property/fuzz/panic/fallback/A1 scheduler and latency evidence
Why current work cannot safely continue: Windows/Rust source tests do not prove Linux ARM64 Rustler scheduling or production artifact behavior.
Minimal provider output required: native-safety result and signed ARM64 artifact evidence bound to the exact release
Temporary work that remains independent: artifact identity and fallback acceptance contracts
```

```text
Blocked seam:
Provider: Senior 4
Consumer: Senior 5
Missing contract: reviewed live plan, deployed host readiness, activation/edge and rollback exercise evidence
Why current work cannot safely continue: source validation and deterministic local bundles do not prove live OCI state, drift, cost/security, retained rollback target or cutover behavior.
Minimal provider output required: provider-owned live evidence for infrastructure-plan, host-readiness and rollback-evidence
Temporary work that remains independent: protected workflows, mutation-log validation and final composer
```

Removal condition: remove a block only after the registered provider command is available, runs on the exact composite SHA/release, and returns schema-valid non-fixture evidence. No readiness-owned emulation satisfies this condition.
