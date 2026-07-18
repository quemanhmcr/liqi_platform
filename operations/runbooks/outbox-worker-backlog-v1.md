# Outbox and Oban backlog V1

## Trigger
- outbox-age-p99 or oban-queue-age-p99 alert fires
- Backlog recovery does not converge after load

## Expected degradation and safety

Outbox remains the durable domain handoff. Oban backlog may delay background work but must not replace or corrupt outbox semantics.

## Operator procedure
1. Stop non-critical job producers and traffic expansion.
2. Capture oldest outbox age, dispatch rate, retry counts, terminal errors, Oban queue age and concurrency.
3. Verify PostgreSQL/PgBouncer health and disk reserve.
4. Ask Senior 2 to execute provider-owned backlog diagnostics and recovery commands.
5. Do not delete, skip or mark durable outbox rows complete merely to reduce age.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Backlogs drain within the scenario budget, terminal failures are accounted for, and no durable event is lost.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
