# ADR 0303: PostgreSQL provider integration V0

- Status: Accepted with one explicit provider gap
- Date: 2026-07-17
- Owner: Senior 3
- Provider baseline: Senior 2 commits `43b95c9` and `6fc4179`

## Context

Senior 2 published the durable authority contract and migration version 2 after the initial Senior 3 runtime ports were drafted. The implementation provides transaction-pooled stored functions for readiness, probe insertion, outbox claim, acknowledgement, bounded retry/dead-letter, and atomic probe-effect acknowledgement.

The original in-memory port draft treated effect insertion and acknowledgement as two calls. That assumption is unsafe because a crash between those calls can commit an effect while leaving the event reclaimable. The provider also restricts `liqi_realtime` from claiming outbox rows or reading authority tables directly.

## Decision

- API and worker connect through PgBouncer at port 6432 in transaction mode.
- SQLx server-side statement caching is disabled and runtime queries are marked non-persistent. No runtime behavior depends on session affinity, temporary tables, `LISTEN/NOTIFY`, or session advisory locks.
- Runtime pool caps are enforced per database role: API 20, realtime 5, worker 10; accepted local examples use 16, 5, and 10 respectively.
- Required migration version is 2.
- The probe client ID is the durable probe ID. Its event ID is derived deterministically with UUIDv5, making a retried API request compatible with `platform.request_probe_v0` idempotency.
- The worker uses `platform.apply_probe_effect_and_ack_v0`; effect and acknowledgement are one durable transaction.
- Claim tokens and realtime cursors remain opaque outside the provider adapter.
- Retry is bounded to eight attempts. The worker calculates capped exponential delay with deterministic jitter and passes an absolute retry timestamp to the provider.
- The V0 adapter maps only `platform.probe.requested.v0`. Adding another event type requires an additive/versioned claim interface because the current provider claim function does not filter by event type.

## Explicit provider gap

Senior 2 has not yet published a committed realtime handoff function, and the `liqi_realtime` role correctly has no outbox-claim or table-read grant. Therefore the production realtime adapter fails closed and adds a `realtime-handoff` readiness failure. It does not bypass grants, read authority tables, or create an in-memory durable sink.

The current outbox row also does not persist producer, correlation ID, causation ID, or bounded metadata. For the platform-only probe, producer is deterministically reconstructed as `liqi-api`; the other optional wire fields are omitted. Before any non-probe event consumer merges, Senior 2 and Senior 3 must publish an additive durable representation/handoff preserving those wire fields or explicitly version the envelope.

## Compatibility and removal conditions

- Contract V0 has no merged external consumer at this checkpoint, so `schemaVersion` was renamed to `eventVersion` before first consumption to match the accepted provider vocabulary.
- Dev/test fake persistence remains behind the compile feature `dev-fakes`, the runtime capability `persistence.fake`, and a local/development/test environment check. Production-like config rejects it.
- The fake is removed from the integration path when the database adapter platform-probe test passes against migration version 2 through PgBouncer.
- Realtime readiness becomes green only after an approved committed-handoff provider and access-revocation provider are integrated.

## Trade-off

This keeps the V0 runtime honest and mergeable now: API and worker exercise the real durable authority, while realtime visibly reports its missing provider rather than fabricating availability. The cost is that the complete HTTP → worker → realtime walking skeleton remains blocked on one additive Senior 2 seam.
