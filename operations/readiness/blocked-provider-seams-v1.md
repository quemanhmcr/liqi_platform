# V1 provider integration and remaining evidence seams

The committed runtime, database, native and infrastructure provider graph is integrated on the composite V1 branch. Provider command publication is complete for source, disposable database/runtime integration, native safety, release/native artifact verification and the runtime live probe. `available` means invokable; it does not mean passed evidence exists.

## Integrated provider commands

| Provider | Exact provider commit | Integrated command | Evidence state |
|---|---|---|---|
| Senior 1 | `15e2dd5a263decb91308a0d1783c4610bd7dc62d` | BEAM source, disposable PostgreSQL integration, signed release verifier, platform probe | Source and disposable integration can pass; Linux ARM64 release and live endpoint evidence pending. |
| Senior 2 | `168f6b3be66ff36eac4b4944f8d6940b6d2026ce` | PostgreSQL contracts and disposable pgTAP integration | Source and disposable integration available; isolated restore/PITR command unpublished. |
| Senior 3 | `ca71a1be6914a33db22544802f704084f3346af5` | Rust source, native safety result, complete deployment-manifest verifier | Command publication complete; passed Linux/A1 safety and signed ARM64 artifact inputs pending. |
| Senior 4 | `ca99b7d14816cd051fce15a54accdeb17276096d` | OCI source/contracts and committed deployment/rollback controls | Source passes without mutation; reviewed live plan, deployed host and rollback exercise evidence pending. |

Senior 1 commit `e9201d742765f4b1c544e60648e0a719eab91c8e` supplies the fail-safe non-Linux native safety adapter consumed by Senior 5. It does not replace passed Linux/A1 evidence.

## Remaining owner seams

```text
Blocked seam:
Provider: Senior 2
Consumer: Senior 5
Missing contract: approved isolated restore/PITR wrapper producing recovery-result-v1
Why current work cannot safely continue: disposable migrations and pgTAP tests do not prove backup freshness, RPO, RTO, invariant checks or cleanup after a restore.
Minimal provider output required: exact-release isolated restore evidence with explicit approval; never restore over live.
```

```text
Blocked seam:
Provider: Senior 1 / Senior 3 / Senior 4
Consumer: Senior 5 artifact checkpoint
Missing evidence: signed Linux ARM64 Mix release and native artifact, SBOM, provenance, Sigstore bundle, Ed25519 deployment signature and trusted key directories
Why current work cannot safely continue: local Windows packaging and verifier publication do not prove deployable AArch64 bytes or trust identity.
Minimal provider output required: exact-release manifests and immutable artifact inputs accepted by both integrated verifiers.
```

```text
Blocked seam:
Provider: Senior 3
Consumer: Senior 5 integration checkpoint
Missing evidence: passed Linux native-safety result with exact OTP/Elixir/Rust, bounded fuzzing, panic/fallback checks and direct A1 scheduler/latency observation
Why current work cannot safely continue: a schema-valid Windows result is intentionally blocked and cannot prove Linux ARM64/BEAM scheduler behavior.
Minimal provider output required: passed native-safety-result-v1 from the approved Linux/A1 evidence environment.
```

```text
Blocked seam:
Provider: Senior 1 / Senior 4
Consumer: Senior 5 live-staging, promotion, cutover and post-cutover checkpoints
Missing evidence: reviewed OCI plan, approved deployment, exact-release host readiness and platform probe, retained rollback target and owner-run rollback result
Why current work cannot safely continue: source validation cannot prove live drift, edge routing, authorization, capacity or rollback behavior.
Minimal provider output required: provider-owned exact-release live evidence and approved mutation log.
```

Removal condition: remove a block only after the command runs for the exact composite SHA/release and returns schema-valid non-fixture evidence. No example, local package or readiness-owned emulation satisfies live, restore, A1 or ARM64 evidence.
