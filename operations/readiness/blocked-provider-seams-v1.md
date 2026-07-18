# V1 provider seam checkpoint

This checkpoint records consumer-ready provider commands integrated through native provider commit `c73a4d15f366bc6675c36458642d3911a58a42fb`. `available` means the command exists on the integrated graph and can be invoked by the readiness runner; it does not mean artifact or live evidence has passed. Checkpoint evidence must bind the resulting composite SHA.

## Integrated provider commands

| Provider | Exact provider commit | Consumer seam | State |
|---|---|---|---|
| Senior 1 | `15e2dd5a263decb91308a0d1783c4610bd7dc62d` | `beam/scripts/validate-v1-source.sh --output` | Available; exact-SHA source evidence passed before this native integration and must be regenerated on the resulting SHA. |
| Senior 1 | `15e2dd5a263decb91308a0d1783c4610bd7dc62d` | `beam/scripts/run-v1-integration.sh --output` | Available; disposable PostgreSQL 17 consumer/provider evidence passed before this native integration and must be regenerated on the resulting SHA. |
| Senior 1 | `15e2dd5a263decb91308a0d1783c4610bd7dc62d` | `beam/scripts/verify-v1-release.sh --manifest ... --output` | Available verifier; signed ARM64 Mix release input is pending. |
| Senior 1 | `15e2dd5a263decb91308a0d1783c4610bd7dc62d` | `beam/bin/platform-probe --output` | Collector integrated; exact-release live evidence is pending. |
| Senior 2 | `168f6b3be66ff36eac4b4944f8d6940b6d2026ce` | database source and disposable integration commands | Available; source and composite disposable database evidence passed before this native integration. |
| Senior 3 | `ca71a1be6914a33db22544802f704084f3346af5` | `bash native/tests/run-source-validation.sh --rust-only`; `bash native/scripts/run-v1-safety-gates.sh --output` | Source and safety commands are available. Full safety evidence requires pinned Linux OTP/Elixir/Rust, bounded fuzzing and direct A1 observation. |
| Senior 3 | `ca71a1be6914a33db22544802f704084f3346af5` | `python native/scripts/verify_deployment_manifest.py --native-manifest ... --deployment-manifest ... --trust-dir ...` | Complete provider-to-deployment verifier is available. Signed ARM64 bytes, SBOM, provenance, Sigstore bundle, Ed25519 signature and A1 evidence remain inputs. |
| Senior 4 | `f4b4563b0d6a5a3dd02c4ffb2a9915c6fb270aad` | `python infrastructure/validation/validate_v1_source.py` | Available; source validation passed before this native integration with no OCI or host mutation. |

## Remaining blocked seams

```text
Blocked seam:
Provider: Senior 1 / Senior 4
Consumer: Senior 5 artifact checkpoint
Missing evidence: signed AArch64 Mix release manifest, archive, signatures and trusted public key material
Why current work cannot safely continue: a local Windows release proves packaging layout only and cannot prove AArch64 ERTS or deployable signature identity.
Minimal provider output required: LIQI_RELEASE_MANIFEST and LIQI_RELEASE_TRUST_DIR inputs accepted by runtime-artifact.
```

```text
Blocked seam:
Provider: Senior 1 / Senior 4
Consumer: Senior 5 live-staging and promotion checkpoints
Missing evidence: approved OCI deployment plus exact-release HTTPS/WebSocket platform probe
Why current work cannot safely continue: source and local integration cannot prove edge routing, secret materialization, drain/restart/resume or live authorization behavior.
Minimal provider output required: LIQI_BASE_URL, LIQI_RELEASE_ID and LIQI_PROBE_AUTH_TOKEN_REF for runtime-live-probe after approved deployment.
```

```text
Blocked seam:
Provider: Senior 2
Consumer: Senior 5 recovery checkpoint
Missing contract: approved isolated restore/PITR collector producing recovery-result-v1 evidence
Why current work cannot safely continue: disposable migrations and database tests do not prove backup freshness, RPO or RTO.
Minimal provider output required: database-recovery command and approved isolated restore result; never restore over live.
```

```text
Blocked seam:
Provider: Senior 3
Consumer: Senior 5 integration and artifact checkpoints
Missing evidence: passed Linux native-safety result, Linux ARM64 Rustler target check, signed ARM64 native artifact and direct A1 scheduler/latency evidence
Why current work cannot safely continue: command publication and Windows source/property/clippy checks do not prove production ARM64/BEAM scheduler behavior.
Minimal provider output required: run the published native-safety command on the pinned Linux toolchain and verify the signed native deployment handoff for the exact release.
```

```text
Blocked seam:
Provider: Senior 4
Consumer: Senior 5 promotion/cutover checkpoints
Missing contracts: reviewed OCI plan, live host-readiness collector and rollback-result collector
Why current work cannot safely continue: source validators and examples cannot prove live drift, host capacity, retained rollback target or approved mutations.
Minimal provider output required: infrastructure-plan, host-readiness and rollback-evidence commands with exact-release results.
```

No fixture, example, local Windows package or readiness-owned wrapper satisfies a live or ARM64 evidence requirement.
