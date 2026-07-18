# Reconnect storm and slow consumer V1

## Trigger
- realtime-resume-success or slow-consumer-rate alert fires
- 25 percent reconnect scenario fails to recover

## Expected degradation and safety

Ephemeral traffic may coalesce or drop. Slow sessions disconnect before unbounded queues; durable events remain available through resume and gap repair.

## Operator procedure
1. Freeze cohort expansion.
2. Capture baseline sessions, disconnect count, reconnect arrival distribution, resume cursor, acknowledged sequence, gap repairs and command-plane errors.
3. Inspect per-session outbound queue capacity/age and actor mailbox distributions.
4. Ask Senior 1 to run the provider-owned drain/resume diagnostics.
5. Rollback if resume success stays below 99 percent, recovery exceeds five minutes, or any durable event is lost.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

The storm recovers within five minutes, resume success is at least 99 percent, and command-plane/correctness objectives remain healthy.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
