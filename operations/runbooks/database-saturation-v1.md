# PostgreSQL and PgBouncer saturation V1

## Trigger
- db-pool-wait-p99 alert fires
- PostgreSQL restart or PgBouncer-unavailable scenario is active

## Expected degradation and safety

New durable commands reject before transaction with retryable errors. Direct recovery connections and committed data remain protected.

## Operator procedure
1. Freeze traffic expansion and avoid retry amplification.
2. Capture PostgreSQL connections/memory, PgBouncer client/server pools and waits, Ecto queue time, transaction errors and outbox age.
3. Ask Senior 2 to run provider-owned readiness and connection-budget diagnostics.
4. Verify no command was acknowledged without commit and no event was published before commit.
5. Rollback the cohort if pool waits remain critical, recovery exceeds the scenario budget or correctness is uncertain.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Pools recover inside budget, durable writes are correct/idempotent and connection/memory reserves remain declared.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
