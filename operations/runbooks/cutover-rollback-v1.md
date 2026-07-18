# Traffic cutover, activation failure and rollback V1

## Trigger
- A phased cutover gate fails
- Release activation fails, host reboots, or rollback criteria are met

## Expected degradation and safety

No big-bang switch. V0 remains retained; route changes are health-gated, resume-aware and approved. Single-node reboot is an outage, not HA.

## Operator procedure
1. Stop new admission and freeze the current cohort.
2. Capture exact V0/V1 release IDs and SHAs, Caddy route/cohort, drain status, readiness probes and database compatibility.
3. Use only the Senior 4 approved deployment/rollback command; the readiness plane does not mutate traffic.
4. Verify health, WebSocket reconnect/resume, outbox age, DB pool, BEAM run queue, native fallback, memory and disk.
5. Observe at least 30 minutes after a promoted phase; rollback immediately on correctness events or unavailable rollback evidence.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

The phase meets SLOs through the observation window, rollback remains proven/retained and all mutation approvals/evidence are present.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
