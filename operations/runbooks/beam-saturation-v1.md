# BEAM scheduler, mailbox and actor saturation V1

## Trigger
- beam run queue, scheduler utilization, actor mailbox age or memory reserve alert fires
- A supervised process or actor partition repeatedly restarts

## Expected degradation and safety

Admission is bounded, affected actors rebuild from PostgreSQL, and at least 1 OCPU plus 4 GiB host reserve remains available.

## Operator procedure
1. Stop traffic expansion and optional background/native work.
2. Capture scheduler utilization, run queue per scheduler, reductions, process count, top mailbox depths/ages, ETS memory and restart intensity.
3. Ask Senior 1 to inspect the provider-owned supervision/admission/partition diagnostics.
4. Confirm no global unbounded mailbox or DynamicSupervisor growth.
5. Rollback if reserve is consumed, restart intensity does not converge, or command/realtime correctness is threatened.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Run queue and mailbox age return below objectives, restart intensity stabilizes, and reserve/correctness gates remain healthy.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
