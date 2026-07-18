# ADR 1001: V1 provider contract mismatches and fail-closed integration

- Status: Accepted for Senior 1 implementation; provider actions pending
- Date: 2026-07-18
- Decision owner: Senior 1
- Consumers: Senior 2, Senior 3, Senior 4, Senior 5

## Context

The V1 provider branches were reviewed and integrated at exact commits. Three assumptions in the published seams do not match the executable runtime graph:

1. Senior 2 publishes `ecto_sql >=3.13,<4.0` with `postgrex >=0.20,<0.21`, but Ecto SQL 3.14 requires Decimal 3 while Postgrex 0.20 requires Decimal 1 or 2. Downgrading to Ecto SQL 3.13 resolves versions only by selecting Postgrex 0.20.0 and Decimal 2.4.1, which Hex currently reports with known HIGH/MEDIUM security advisories. The safe resolved graph is Ecto SQL 3.14.0, Postgrex 0.22.3 and Decimal 3.1.1.
2. Senior 2 has published migration-8 semantics but no migration-8 SQL or callable function signatures. Senior 1 cannot safely infer function names, argument order, SQLSTATE mapping or handoff watermark results from prose/schema alone.
3. Senior 4's initial release example uses OTP 29/Elixir 1.19 and `bin/... rpc` health/drain commands, while Senior 1 pins OTP 28.5.0.3/Elixir 1.20.2 and deliberately sets `RELEASE_DISTRIBUTION=none`. Enabling distribution solely for local operator RPC would expand the network/cookie attack surface without a V1 clustering requirement.

A clean production release also exposed unreachable-clause warnings in the Senior 3 Elixir wrapper. Cached compilation must not allow those warnings to disappear from source evidence.

## Decision

- Keep the audited runtime dependency graph: Ecto SQL 3.14.0, Postgrex 0.22.3, Decimal 3.1.1 and Oban 2.23.0.
- `Liqi.Persistence.PostgresV1` remains the production default and fails closed with `database_v1_provider_unavailable` until Senior 2 publishes exact migration-8 callable seams and updates the audited compatibility range.
- `Liqi.Persistence.PostgresV0` remains an explicit non-default rollback-window adapter. It is never selected automatically and must be removed when the V0 route rollback window closes.
- Runtime source evidence requires a clean worktree and clean dependency compilation. Cached `_build` state is not acceptable evidence.
- Keep Erlang distribution disabled. Release health and drain are bounded loopback HTTP executables installed as `bin/liqi-health` and `bin/liqi-drain`. Drain authorization is materialized only from a file or systemd credential reference.
- Release artifact verification inspects the actual ERTS ELF header and requires `EM_AARCH64`, rather than trusting `target_triple` text in a manifest.

## Provider actions

### Senior 2

Publish additive migration 5–8 source and exact callable interfaces for:

- migration/write readiness at required version 8;
- durable idempotent platform-probe command;
- outbox claim/apply/fail semantics;
- V1 committed handoff read including retained watermark/gap result;
- least-privilege terminal probe observation for integration/live evidence.

Update the compatibility contract to an audited Postgrex range compatible with the selected Ecto SQL graph. Do not require Senior 1 to downgrade to a dependency set with known advisories.

### Senior 3

Repair Elixir wrapper warnings under a clean `mix compile --warnings-as-errors`. Optional missing ARM64 artifacts may degrade readiness, but provider source warnings may not be hidden by a warm build cache.

### Senior 4

Consume Senior 1's exact toolchain and loopback command arrays in the release manifest. Do not introduce distributed Erlang or a shell-evaluated RPC string. Build/sign/install still belongs to Senior 4 and must occur on an approved AArch64 environment.

### Senior 5

Keep runtime integration blocked until the Senior 2 callable seam is integrated. Register source/artifact/live commands against exact provider commits and reject evidence produced from a dirty worktree or non-AArch64 ERTS.

## Compatibility and migration

The change is additive at the wire level. Database writes remain disabled rather than falling back to V0. V0 rollback is route-scoped and single-writer; there is no durable dual write. Once Senior 2 publishes the exact V1 seam, Senior 1 replaces the fail-closed adapter and adds disposable PostgreSQL contract tests in one integration commit.

## Temporary implementation

- Owner: Senior 1.
- Temporary component: `Liqi.Persistence.PostgresV1` fail-closed placeholder.
- Removal condition: exact Senior 2 migration-8 callable provider commit is integrated and provider/consumer integration tests pass.
- Production default: yes, but it rejects all V1 durable commands and readiness remains red; it never redirects to V0.

## OCI impact

None. No OpenTofu apply, live migration, release deployment, backup/restore or traffic mutation was performed.
