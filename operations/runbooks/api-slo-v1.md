# API availability and latency V1

## Trigger
- api-availability, api-latency-p95 or api-latency-p99 alert fires
- A cohort expansion gate observes elevated HTTP errors or latency

## Expected degradation and safety

Admission control rejects excess work before transaction. Existing durable work, readiness probes and rollback controls retain priority.

## Operator procedure
1. Freeze traffic cohort expansion.
2. Capture exact release ID, Git SHA, Phoenix request/error/latency histograms, BEAM run queue, scheduler utilization and database pool wait.
3. Ask Senior 1 to inspect the provider-owned runtime health, admission and drain commands; do not patch runtime behavior from the readiness plane.
4. Compare the same interval with outbox age, native latency and host reserve to identify the owning seam.
5. Rollback the cohort when the critical threshold persists for two consecutive five-minute windows or a correctness event occurs.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

The objective is healthy for one complete observation window, host reserve remains intact, and no correctness event occurred.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
