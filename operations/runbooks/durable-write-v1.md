# Durable write success V1

## Trigger
- durable-write-success alert fires
- Retryable command rejection rises or committed-event evidence is missing

## Expected degradation and safety

Commands are rejected before transaction when capacity is unavailable. No uncommitted command is acknowledged and no event is published before commit.

## Operator procedure
1. Stop promotion and traffic expansion.
2. Capture idempotency result, transaction result, aggregate version, outbox row and error classification for representative failures.
3. Ask Senior 2 to run the provider-owned database readiness and outbox validation commands.
4. Correlate Ecto pool wait, PgBouncer availability, PostgreSQL errors and outbox age.
5. Escalate any event-before-commit, duplicate durable identity or durable event loss as a zero-tolerance security/correctness incident.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Writes meet the objective, retry semantics remain idempotent, and all correctness counters are zero.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
