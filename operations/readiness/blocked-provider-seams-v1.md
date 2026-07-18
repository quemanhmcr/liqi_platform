# V1 blocked provider seams

This is a machine-reviewable integration checkpoint, not a substitute implementation. Exact provider commits are recorded only where the directly consumable source command exists. `pending-integration` still blocks Senior 5 until that commit is integrated and passes on the resulting repository SHA.

## Published, pending integration

| Provider | Exact commit | Consumer seam | Current limitation |
|---|---|---|---|
| Senior 2 | `ac759bb3435ef4633265d8eab75bd26768c0aac9` | `python database/tests/contract/validate_v1_contracts.py` | Source contracts only; disposable PostgreSQL integration and approved isolated restore result are not published. |
| Senior 3 | `7478e31a4de48e278f0d08885bfaab56d5d88762` | `bash native/tests/run-source-validation.sh --rust-only` | Source/Rust safety is available after integration; full Elixir, fuzz-duration, A1 latency and scheduler evidence remain pending. |
| Senior 3 | `7478e31a4de48e278f0d08885bfaab56d5d88762` | `python native/scripts/verify_artifact.py --manifest <path>` | Verifier exists; signed ARM64 artifact, SBOM, provenance and Sigstore bundle must be produced by the approved provider flow. |
| Senior 4 | `2be16b0ff7159ad0827194c0f72f5a540245a085` | `python infrastructure/validation/validate_v1_contracts.py` | Source contracts explicitly remain `engineering-complete-evidence-pending`; plan, host, activation and rollback evidence are not published. |

## Still missing provider publication

```text
Blocked seam:
Provider: Senior 1
Consumer: Senior 5
Missing contract: runtime source/integration/artifact/live platform probe commands
Why current work cannot safely continue: Phoenix, OTP lifecycle, envelope, release metadata, drain and realtime semantics are provider-owned.
Minimal provider output required: commands registered under runtime-source, runtime-integration, runtime-artifact and runtime-live-probe
Temporary work that remains independent: readiness schemas, evidence composer, load/reconnect workload and runbooks
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
