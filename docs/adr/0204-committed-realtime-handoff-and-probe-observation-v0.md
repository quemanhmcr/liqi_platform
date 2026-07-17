# ADR 0204: Committed realtime handoff and promotion probe observation V0

## Status

Accepted.

## Context

Senior 3's runtime owns the realtime adapter and platform-probe runner. PostgreSQL remains the only durable authority. Realtime must not claim the worker outbox, acknowledge events, or read authority tables directly. The promotion runner also needs bounded evidence that a probe completed, its outbox event succeeded and its terminal effect was applied idempotently, without retaining the temporary direct-table query.

PgBouncer remains in transaction mode. A `LISTEN/NOTIFY` or session-state design would create a special connection boundary and would not provide replay after process restart. Reading the mutable outbox directly would couple realtime to worker claim state and duplicate lifecycle semantics.

## Decision

Migration `000000000004_runtime_persistence_handoffs.sql` publishes two additive provider seams.

### Committed realtime handoff

Producer function:

```sql
platform.publish_realtime_handoff_v0(requested_event_id uuid) RETURNS bigint
```

Reader function:

```sql
platform.read_realtime_handoff_v0(
  after_handoff_id bigint DEFAULT 0,
  batch_size integer DEFAULT 64
)
RETURNS TABLE (
  handoff_id bigint,
  event_id uuid,
  schema_version smallint,
  event_type text,
  event_version integer,
  occurred_at timestamptz,
  producer text,
  correlation_id uuid,
  causation_id uuid,
  aggregate_key text,
  ordering_key text,
  payload jsonb,
  metadata jsonb,
  recorded_at timestamptz
)
```

`liqi_api` may publish an existing immutable outbox event in the same producer transaction. `platform.request_probe_v0(...)` does this automatically. The handoff row is invisible to other sessions until that transaction commits. V0 accepts only `platform.probe.requested.v0` event version `0`; all other event types fail with SQLSTATE `0A000`. Business realtime publication requires a versioned migration that defines retention, replay-gap and resynchronization semantics.

`liqi_realtime` may only execute the reader. It receives at-least-once rows where `handoff_id > after_handoff_id`, ordered by `handoff_id`, with a default batch of 64 and a hard maximum of 128. The consumer persists the last delivered handoff ID and may replay rows after restart. The function performs no claim or acknowledgement. Realtime has no direct table grant on either the handoff ledger or outbox.

A singleton counter row is locked and incremented inside the producer transaction. The lock is retained until commit, so a later publisher cannot commit a larger handoff ID before an earlier publisher. This creates commit-serialized ordering only for events explicitly selected for realtime; it is not a global ordering claim for all outbox events.

### Probe observation

```sql
platform.observe_probe_v0(
  requested_probe_id uuid,
  requested_event_id uuid
)
RETURNS TABLE (
  probe_id uuid,
  event_id uuid,
  probe_status text,
  outbox_state text,
  effect_applied boolean,
  probe_completed_at timestamptz,
  effect_applied_at timestamptz,
  terminal boolean,
  observed_at timestamptz
)
```

Only `liqi_readonly` may execute this function. It is used by one bounded connection during promotion or disposable integration tests. It is not a long-lived application repository. A missing or mismatched probe/event pair returns no row. Terminal success means:

```text
probe_status = completed
AND outbox_state = succeeded
AND effect_applied = true
```

Direct `SELECT` on `platform.probe_state_v0`, `platform.probe_effects_v0` and `platform.outbox_events` is not granted to `liqi_readonly`.

### Readiness and connection allocation

All API, realtime and worker processes call:

```sql
platform.database_readiness_v0(4)
```

Database unavailability, a required version below 4, or a failed migration makes the process not ready. Liveness remains separate.

PostgreSQL `max_connections=80` is partitioned as:

```text
35 runtime pooled: API 20 + realtime 5 + worker 10
5 operational pooled: readonly 3 + monitor 2
10 direct administrative/recovery
30 reserved headroom, including PostgreSQL superuser reserve and incident capacity
```

PgBouncer remains transaction pooled with `max_db_connections=40`. The promotion observer consumes at most one of the three readonly pool slots and exists only for the probe run.

## Trade-offs

The handoff counter serializes only V0 platform-probe publication transactions selected for realtime. Growth is bounded by promotion-probe frequency and the 8 GiB database data cap; non-probe publication is rejected. This is acceptable for V0 probe/platform traffic and provides an unambiguous replay cursor. If measured publication contention becomes material, a versioned sharded ordering design may replace it; the V0 reader remains supported during migration.

Polling adds bounded database reads. It avoids a new broker, session-pinned PostgreSQL connections and unreplayable notifications. Batch limits, role pool caps and cursor persistence bound the cost.

## Compatibility and consumer action

The change is additive and does not alter outbox claim, retry, acknowledgement or dead-letter semantics.

Senior 3 must:

1. read committed events only through `platform.read_realtime_handoff_v0(bigint, integer)` using `liqi_realtime`;
2. replace the temporary direct-table promotion query with `SELECT * FROM platform.observe_probe_v0($1, $2)` using one `liqi_readonly` connection;
3. remove that temporary query in the first additive integration commit after this provider commit;
4. configure all three processes with required migration version 4.

Senior 3 continues to own wire-envelope mapping, realtime delivery and runner logic. Senior 2 does not implement runtime glue.
