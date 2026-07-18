# ADR 1005: V1 runtime provider publication checkpoint

- Status: Accepted for provider publication; integration, artifact and live evidence pending
- Date: 2026-07-18
- Decision owner: Senior 1
- Consumers: Senior 2, Senior 3, Senior 4, Senior 5

## Context

The BEAM runtime now consumes the committed PostgreSQL, jobs and native provider seams and publishes all four Senior 1 provider commands. The production-readiness registry still references the initial runtime skeleton commit and old OTP/Elixir versions, so it cannot be treated as proof that the current provider branch was integrated. Source evidence is generated outside Git and must be bound to the exact composite SHA after this checkpoint commit.

## Published provider seams

- Source: `bash beam/scripts/validate-v1-source.sh --output <result.json>`
- Disposable integration: `LIQI_TEST_DATABASE_URL=<loopback-trust-postgres-admin-url> bash beam/scripts/run-v1-integration.sh --output <result.json>`
- Artifact: `bash beam/scripts/verify-v1-release.sh --manifest <signed-manifest.json> --trust-dir <trusted-public-key-dir> --output <result.json>`
- Live: `LIQI_PROBE_AUTH_TOKEN_REF=<protected-secret-ref> beam/bin/platform-probe --base-url <https-url> --release-id <release-id> --output <result.json>`

The commands are bounded, emit schema-validated machine-readable evidence, redact protected values and kill timed-out process trees. The disposable integration command rejects remote, password-bearing, query-bearing and non-`postgres` administrative targets before any mutation.

## Current capability state

- Runtime contracts, Phoenix HTTP/WebSocket, session lifecycle, bounded queues, ACK/resume/gap repair, access revocation, slow-consumer handling, graceful drain and provider-native fallback are source-ready.
- The root release builds locally with ERTS and contains `liqi-health` and `liqi-drain` overlays. This is packaging evidence only; a Windows release is not an ARM64 artifact.
- Exact Senior 2 migration/provider gates and exact Senior 3 branch ancestry are present in the runtime branch.
- PostgreSQL remains the only durable authority. There is one root Repo set, one Oban instance and no dual write.

## Blocked seams

### Native artifact and warning-free release build

Provider: Senior 3

Consumer: Senior 1 release; Senior 4 artifact installation; Senior 5 artifact/readiness gates

Missing output: a warning-free `liqi_native` release compilation plus signed AArch64 artifact and direct A1 benchmark/scheduler evidence.

Why current work cannot safely continue: local release packaging emits unreachable `catch` clause warnings in `Liqi.Native.SequenceDiff`. Optional fallback remains correct, but artifact promotion cannot claim a clean production build or native readiness.

Minimal provider output required: an exact Senior 3 commit that compiles `liqi_native` without warnings and the existing signed artifact/benchmark evidence defined by the native contracts.

Temporary work that remains independent: native mode stays optional or disabled; the pure Elixir reference path remains deployable and tested. No temporary native implementation exists in Senior 1 code.

### Runtime secret materialization

Provider: Senior 4

Consumer: Senior 1 runtime

Missing output: provider-owned beam secret mappings for the bounded database role URL bundle, endpoint secret, drain token and platform-probe token, plus Caddy forwarding without token logging.

Why current work cannot safely continue: placeholder secret mappings do not prove that production runtime configuration can materialize all required references.

Minimal provider output required: versioned mapping names and owner-readable materialized files referenced by `runtime-config-v1`.

Temporary work that remains independent: local/test references remain supported; production validation fails closed when required references are absent.

### Readiness registry integration

Provider: Senior 5

Consumer: V1 readiness composition

Missing output: registry entries for the current Senior 1 provider commit, OTP 28.5.0.3 / Elixir 1.20.2, the four published commands, and `LIQI_PROBE_AUTH_TOKEN_REF` for live probes.

Why current work cannot safely continue: the current registry still points to the initial runtime skeleton and marks integration, artifact and live commands unpublished.

Minimal provider output required: a Senior 5-owned registry commit that marks seams available only after running them on the integrated SHA.

Temporary work that remains independent: Senior 1 can generate provider-local evidence, but it is not a readiness verdict and is not committed as production evidence.

## Evidence classification

Until disposable PostgreSQL, signed ARM64 artifact and live OCI evidence exist, the task status is:

```text
engineering-complete-evidence-pending
```

It must not be reported as production-ready, artifact-passed, live-validated or ready for cutover.

## Compatibility and rollback

HTTP/error/event/realtime contracts remain versioned. V0 Rust remains the route-scoped rollback target, application rollback does not run down migrations, and V1 never dual-writes durable state. Actor/session state is rebuildable and requires no migration.

## OCI impact

None. No OpenTofu apply, live migration, deployment, secret mutation, backup/restore, traffic change or production endpoint probe was performed by Senior 1.
