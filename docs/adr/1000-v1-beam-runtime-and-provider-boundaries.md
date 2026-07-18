# ADR 1000: V1 BEAM runtime and provider boundaries

- Status: Accepted for Senior 1 source implementation; provider and OCI evidence pending
- Date: 2026-07-18
- Decision owner: Senior 1
- Affected consumers/providers: Senior 2, Senior 3, Senior 4, Senior 5
- Source baseline: `main@4c561515f46237acfaf64e0145e37e54a6c4c9d9`

## Context

V1 needs a production-shaped BEAM runtime without turning process memory, PubSub, native code, or a compatibility adapter into durable authority. The approved V0 readiness tag does not yet exist, and the integrated V0 closeout still requires owner-build and live evidence. V1 source work can proceed, but neither live readiness nor a production capability claim is permitted.

The reviewed provider commits are:

- Senior 2: `ac759bb` (durable semantic contracts, no callable migration/function seam yet).
- Senior 3: `b82c771` and `7478e31` (bounded sequence diff implementation and artifact gates).
- Senior 4: `2be16b0` (release/systemd/edge contracts).
- Senior 5: `ae24273` (readiness provider gate registry).

## Decision

1. Use one root Mix project and one `liqi_platform` release. Runtime source remains under `beam/**`; native code remains a provider-owned path dependency under `native/elixir`.
2. Use Bandit as the Phoenix HTTP/WebSocket adapter. Both complete and fragmented WebSocket messages are capped at 65,536 bytes and compression is disabled for V1.
3. Use partitioned actor, admission, and read-coalescing supervisors. Session actors are rebuildable and separate from transport connection processes. No process is durable authority.
4. Reject before work for endpoint, database, reconnect, native, and bounded task budgets. Session queues have item, byte, and age ceilings; overflow disconnects, clears ephemeral backlog, and requires PostgreSQL cursor repair.
5. PostgreSQL V1 is the only production persistence path. Until Senior 2 publishes migration 8 plus callable functions, `Liqi.Persistence.PostgresV1` fails readiness and all operations closed. The V0 adapter is route-scoped rollback compatibility only and is never the V1 default.
6. Durable idempotency uses explicit scope, key, fingerprint, expected aggregate version, and a stable event identity. The active actor serializes calls but does not cache a durable outcome or replace database idempotency.
7. The native kernel is optional acceleration. Elixir owns admission, deadline, telemetry, and session lifecycle. Required native mode fails readiness closed; optional unavailable/incompatible/panic results may use the provider reference path.
8. Keep Distributed Erlang disabled in V1. Health and drain use bounded loopback HTTP commands with a materialized drain-token reference. Senior 4's manifest schema accepts command arrays, so the published release manifest will use release-contained `bin/health` and `bin/drain` commands rather than `rpc`.
9. Runtime configuration is versioned JSON. Production secrets are references only (`file://`, `systemd-credential://`, or `oci-vault://`); only already-materialized local values are read by the release.
10. V0 remains a route-scoped rollback target. There is no durable dual write, actor-state migration, or protocol-v1 event delivery into the V0 handoff.

## Database dependency contract mismatch

Senior 2 currently publishes Postgrex `>=0.20,<0.21` with Ecto SQL `>=3.13,<4`. Resolution against Ecto SQL 3.14 fails because the Decimal major ranges are incompatible. Resolving with Ecto SQL 3.13.5 selects Postgrex 0.20.0 and Decimal 2.4.1, which `mix hex.audit` reports with one HIGH Postgrex advisory and one MEDIUM Decimal advisory. V1 therefore uses the audited graph Ecto SQL 3.14.0, Ecto 3.14.1, Postgrex 0.22.3, and Decimal 3.1.1 while the database adapter remains fail-closed.

Proposed provider change:

- Update the database runtime compatibility contract to Postgrex `>=0.22.3,<0.23` and Ecto SQL `>=3.14,<4`, or publish a separately audited patched range.
- Publish migration 8 and exact function signatures/results for command idempotency, probe transaction, outbox claim/effect/failure, migration readiness, and V1 handoff gap detection.
- Preserve SQLSTATE `LQ001`, `LQ002`, and `LQ004` and the existing durable semantics.

Compatibility path: no consumer is switched to V1 database execution until both dependency and callable seams are published. The V0 rollback adapter remains explicit and non-default.

## Blocked seam

```text
Blocked seam: database-v1 callable runtime provider
Provider: Senior 2
Consumer: Senior 1
Missing contract: migration 8 implementation, exact SQL function names/arguments/result columns, and an audited Postgrex range
Why current work cannot safely continue: using V0 functions would send protocol-v1 work through the wrong idempotency and handoff semantics; using the published Postgrex range knowingly selects vulnerable packages
Minimal provider output required: additive migration 8, function grants for liqi_api/liqi_realtime/liqi_worker, executable source validation, and a dependency contract compatible with mix hex.audit
Temporary work that remains independent: fail-closed V1 adapter, test-only fake, HTTP/session/outbox/handoff consumer contract tests, release and native integration
```

## Capacity and operation

The initial BEAM hard ceiling is 1.45 OCPU and 6,144 MiB inside the provider envelope; the VM starts with 3 normal schedulers, 2 dirty CPU schedulers, 2 dirty IO schedulers, 4 async threads, and process limit 65,536. Native concurrency is 2. Oban remains disabled until migration 14 is present; its declared queues total six active slots plus one recovery slot. These are source declarations, not OCI measurements.

## Compatibility and removal

- Remove `Liqi.Persistence.PostgresV0Compatibility` after the V0 route rollback window closes and V1 database integration evidence passes.
- Remove the test fake from any integration profile when the real provider becomes executable; it remains allowed only in `MIX_ENV=test` until then.
- Keep runtime config V0 field aliases through one release window, then remove with a versioned migration note.
- Native artifact rollback requires no database action; reference semantics remain deployable.
- Live OCI deployment, migration, traffic, backup, restore, and rollback remain approval-gated and owner-executed.
