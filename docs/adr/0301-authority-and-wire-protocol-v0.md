# ADR 0301: Authority and wire protocol V0

- Status: Accepted
- Date: 2026-07-17
- Owner: Senior 3

## Context

API, realtime, and worker processes need shared wire semantics without creating a second authority or claiming delivery guarantees that the initial architecture cannot provide.

## Decision

- PostgreSQL owns durable state, event lifecycle, ordering state, and idempotency records.
- A durable command returns success only after its state and outbox event commit atomically.
- Realtime publishes only committed events represented by `event-envelope-v0`.
- Worker delivery is at-least-once. Consumers must be idempotent. No exactly-once statement is permitted in code, contract, metrics, or operations documentation.
- Event IDs are immutable. Event types include a terminal `.vN` version suffix and carry an integer schema version.
- Realtime protocol negotiation is explicit. V0 accepts only protocol version `0`; unsupported versions fail closed.
- Resume cursors are opaque. The runtime does not infer database sequence semantics before Senior 2 defines the persistence contract.
- Per-connection outbound channels are bounded. A full queue produces a slow-consumer signal when possible and then disconnects; the server never grows memory without bound.
- Access revocation immediately stops delivery for the affected subscription. Authorization implementation is outside V0, but the protocol behavior is reserved now.
- Wire errors contain stable code, status, retryability, request/trace correlation, and safe text only. Internal causes remain in structured logs and traces.

## Compatibility

Before the first external consumer merges, V0 may evolve quickly. After consumption:

- Additive optional fields are allowed.
- Breaking changes require a new protocol/schema version or an adapter for one release window.
- Published error codes cannot be reused for different semantics.
- Removal conditions and consumers must be recorded in the integration commit note.

## Consequences

The first persistence adapter can map durable outbox records into the shared envelope without changing API or realtime consumers. A fake provider can exercise orchestration in dev/test but cannot invent claim, cursor, acknowledgement, or retry semantics.
