# Realtime delivery latency V1

## Trigger
- commit-to-deliver p95/p99 alert fires
- Platform probe cannot observe committed outbox delivery

## Expected degradation and safety

Durable events may be delayed but remain retained and repairable. Realtime degradation must not pull down the command plane.

## Operator procedure
1. Freeze cohort expansion and preserve durable command admission headroom.
2. Capture commit timestamp, stable event ID, ordering key, outbox handoff timestamp, delivery timestamp and session cursor.
3. Inspect outbound queue age, slow-consumer disconnects, actor mailbox age and outbox age.
4. Ask Senior 1 and Senior 2 to validate their respective runtime and committed-handoff seams.
5. Rollback when durable delivery cannot be repaired, command-plane SLO degrades, or any durable event is lost.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Delivery objectives recover, gap repair succeeds, and durable event loss remains zero.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
