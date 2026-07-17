# ADR 0201: Forward-only migrations and durable outbox V0

## Status

Accepted.

## Context

LIQI requires independent API, realtime and worker development while retaining one durable interpretation of state. V0 must establish schema lifecycle and event handoff without introducing product-domain tables or claiming exactly-once delivery.

## Decision

### Migration lifecycle

- Migration files are immutable after merge to `main` and named `<12-digit-version>_<name>.sql`.
- A SHA-256 manifest is committed beside the files. The runner rejects a local file whose checksum differs from the manifest and rejects an already-applied database checksum mismatch.
- One runner holds a PostgreSQL session advisory lock for the entire migration run.
- Each migration is applied in its own transaction and recorded in `platform.schema_migrations` only after the migration succeeds.
- Application rollback never requires reversing a destructive database migration. Schema changes follow expand–migrate–contract.
- Runtime readiness receives the application-required migration version and fails closed when the database is behind or a failed migration run is recorded.

The runner is intentionally a thin `psql` orchestration layer over PostgreSQL advisory locks and transactions. It is not a second schema engine.

### Durable outbox

- A producer inserts business/platform state and its outbox row in the same PostgreSQL transaction.
- An event becomes observable only after that transaction commits.
- The durable representation preserves event ID, schema/type version, occurrence time, producer, correlation/causation IDs, aggregate key, ordering key, payload and metadata.
- Ordering is scoped to an explicit ordering key. No global event order is promised.
- Workers claim bounded batches with `FOR UPDATE SKIP LOCKED`, a unique claim token and an expiring lease.
- Delivery is at-least-once. A consumer must make its terminal effect idempotent.
- Acknowledgement is idempotent for an already-succeeded event. Stale claim tokens cannot acknowledge or fail a newer lease.
- Retry is bounded. Exhaustion moves the event to a retained dead-letter state for operator action.
- Realtime publication must follow commit and must not treat an in-memory broadcast as authority.

## Walking skeleton

The only V0 event type is `platform.probe.requested.v0`. It proves atomic state/outbox insertion, exclusive leases, lease reclaim, idempotent terminal effect, bounded retry, backup inclusion and restore verification. It creates no PlayerId or LIQI business lifecycle.

## Consumer compatibility

Senior 3 owns the wire envelope. Senior 2 owns the durable storage representation. A mapping test must prove that conversion does not lose event ID, type/version, occurred-at, aggregate key, ordering key or payload. Any lifecycle or field removal is a contract change requiring a versioned migration path.
