# V1 blocked provider seams

This is a machine-reviewable integration checkpoint, not a substitute implementation. Exact provider commits are recorded only where the directly consumable source command exists. `pending-integration` still blocks Senior 5 until that commit is integrated and passes on the resulting repository SHA.

## Published, pending integration

| Provider | Exact commit | Consumer seam | Current limitation |
|---|---|---|---|
| Senior 1 | `9a95350c516baa0b6e079685e1dcab1a49799bdf` | `MIX_ENV=test mix compile --warnings-as-errors && MIX_ENV=test mix test --seed 0 && mix hex.audit && python scripts/operations/validate_contracts.py` | Source walking skeleton only; disposable PostgreSQL integration, release artifact verification and live platform probe command are not published. |
| Senior 2 | `ac759bb3435ef4633265d8eab75bd26768c0aac9` | `python database/tests/contract/validate_v1_contracts.py` | Source contracts only; disposable PostgreSQL integration and approved isolated restore result are not published. |
| Senior 3 | `ca71a1be6914a33db22544802f704084f3346af5` | `bash native/tests/run-source-validation.sh --rust-only`; `bash native/scripts/run-v1-safety-gates.sh --output <path>` | Source and safety provider commands are integrated. Full safety evidence still requires pinned Linux OTP/Elixir/Rust plus bounded cargo-fuzz. |
| Senior 3 | `ca71a1be6914a33db22544802f704084f3346af5` | `python native/scripts/verify_deployment_manifest.py --native-manifest <path> --deployment-manifest <path> --trust-dir <path>` | Complete provider-to-deployment verifier is integrated. Signed ARM64 bytes, SBOM, provenance, Sigstore bundle, Ed25519 signature and A1 evidence remain exact-release inputs. |
| Senior 4 | `2be16b0ff7159ad0827194c0f72f5a540245a085` | `python infrastructure/validation/validate_v1_contracts.py` | Source contracts explicitly remain `engineering-complete-evidence-pending`; plan, host, activation and rollback evidence are not published. |

## Still missing provider publication

```text
Blocked seam:
Provider: Senior 1
Consumer: Senior 5
Missing contract: disposable PostgreSQL runtime integration, release artifact verification and live platform probe commands
Why current work cannot safely continue: the published source walking skeleton uses a fail-closed database adapter and a local E2E probe; it does not prove live PostgreSQL, release packaging or OCI endpoint semantics.
Minimal provider output required: commands registered under runtime-integration, runtime-artifact and runtime-live-probe
Temporary work that remains independent: exact source checkpoint, load/reconnect workload and live evidence schemas
```


```text
Blocked seam:
Provider: Senior 2
Consumer: Senior 5
Missing contract: disposable database integration command and approved isolated restore/PITR result
Why current work cannot safely continue: outbox, Oban, migration, connection and restore semantics must be proven by the authority owner.
Minimal provider output required: database-integration and database-recovery commands in the registry
Temporary work that remains independent: recovery acceptance validator and exact-release composer
```

```text
Blocked seam:
Provider: Senior 3
Consumer: Senior 5
Missing contract: one command producing full property/fuzz/panic/fallback/A1 scheduler and latency evidence
Why current work cannot safely continue: source tests and an artifact verifier do not prove live ARM64/BEAM safety.
Minimal provider output required: native-safety result bound to the exact release and host shape
Temporary work that remains independent: artifact identity and fallback acceptance contracts
```

```text
Blocked seam:
Provider: Senior 4
Consumer: Senior 5
Missing contract: reviewed OCI plan, live host readiness, activation and rollback collectors
Why current work cannot safely continue: examples cannot prove live endpoint, drift, cost/security, retained rollback target or approved mutations.
Minimal provider output required: infrastructure-plan, host-readiness and rollback-evidence commands
Temporary work that remains independent: protected workflows, mutation log validation and cutover composer
```

Removal condition: delete each block only after its exact provider commit is integrated, the registered command is marked `available`, and provider/consumer contract tests pass. No fixture or readiness-owned wrapper satisfies this condition.
