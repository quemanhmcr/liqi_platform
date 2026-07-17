# ADR 0415: Runtime provider operability integration

## Status

Accepted for V0 integration.

## Context

Senior 3 published commit `7ed9cc9` with the pinned Rust workspace, runtime configuration schema, API/realtime/worker skeletons, OpenAPI/realtime/error/event contracts, PostgreSQL adapters, health endpoints, artifact metadata and a durable probe walking skeleton.

Senior 4 must consume provider seams without compiling the workspace automatically, weakening promotion evidence or manufacturing runtime glue.

## Decision

1. Source CI may run only the published non-building commands:
   - `cargo +1.97.1 fmt --all -- --check`;
   - `cargo +1.97.1 metadata --no-deps --format-version 1 --locked`.
2. `cargo run`, `cargo clippy` and `cargo test` gates use provider state `pending-owner-build`. They remain blocked until the project owner runs them and supplies evidence.
3. Provider stdout JSON is materialized by the generic `stdout-json` result mode. Senior 4 does not parse Rust source to recreate `liqi-platform-tool` output.
4. Liveness/readiness release identity is `HealthResponse.version`. Deployment sets runtime `service.version` to the release ID. Artifact metadata uses the same version and additionally carries `sourceRevision`.
5. The event example passes Senior 2's lossless wire-to-durable mapping for the full V0 envelope, including producer, correlation/causation IDs and metadata.
6. Promotion remains fail-closed until Senior 3 publishes:
   - `contracts/platform/runtime-capacity-budget-v0.json`;
   - telemetry capability declarations for API, realtime and worker satisfying `telemetry-v0`;
   - a provider-owned runner producing `platform-probe-result-v0`.
7. `POST /platform/v0/probes` proves atomic durable acceptance only. It does not replace terminal outbox, worker effect, realtime delivery or release-observation evidence.
8. Realtime readiness calls the committed handoff provider and remains down until that read is usable.
9. Owner evidence uses `owner-build-evidence-v0`, exact registry argv, exact Git SHA and checksummed outputs. Invalid evidence is failed, not blocked.

## Consequences

- Source CI gains useful Rust format/workspace validation without violating the owner-only build rule.
- Strict integration remains blocked rather than silently skipping clippy, tests, telemetry or the end-to-end platform probe.
- Senior 3 keeps ownership of runtime implementation and build semantics.
- Senior 4 owns only evidence orchestration and compatibility policy.

## Removal condition

`pending-owner-build` remains the registry state while source CI is forbidden to compile. A gate becomes passed only when exact-SHA owner evidence is supplied; removal requires an approved automated build policy.

## Provider update after initial acceptance

Senior 3 commits `dd8d643` and `c8d9b96` satisfy the source-level capacity, telemetry and platform-probe publication actions. Senior 4 marks those seams available and validates capacity/telemetry with a non-building shared-schema gate. The four explicit Cargo build-dependent commands remain `pending-owner-build`. Promotion consumes the integrated committed realtime handoff and probe observation, and still fails closed without owner-built runtime evidence. CPU accounting is refined by ADR 0416 rather than reducing Senior 3's truthful 1.45 OCPU hard runtime ceiling.
