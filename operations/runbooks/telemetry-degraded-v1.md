# Telemetry sink unavailable V1

## Trigger
- Telemetry export fails or local telemetry queue reaches its bound

## Expected degradation and safety

Telemetry samples or drops before workload impact. Functional traffic continues with a local degraded signal and bounded disk/memory use.

## Operator procedure
1. Capture exporter errors, queue depth, dropped spans/metrics/logs, local disk and workload latency.
2. Confirm retry/queue limits and that telemetry does not block Phoenix, outbox, Oban or native execution.
3. Preserve local security/correctness signals and journal retention.
4. Ask Senior 4 to inspect collector/systemd configuration and Senior 1 for instrumentation pressure.
5. Stop promotion if required SLI data becomes unavailable.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Exporter recovers, queues drain within bounds, workload SLOs remain healthy and required readiness evidence is complete.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
