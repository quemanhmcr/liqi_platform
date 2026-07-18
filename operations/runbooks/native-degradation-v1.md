# Rustler native degradation and fallback V1

## Trigger
- native p99 or fallback error alert fires
- Native artifact is disabled, missing, panics or reports bounded-input violation

## Expected degradation and safety

Native code is acceleration only. Feature flags select the pure Elixir reference path without changing durable semantics.

## Operator procedure
1. Disable the affected native kernel through the provider-owned feature flag; do not alter artifact files from the readiness plane.
2. Capture artifact identity, NIF ABI, target triple, scheduling class, input size, concurrency guard, latency and panic/error mapping.
3. Verify BEAM scheduler/run queue and native memory remain bounded.
4. Ask Senior 3 to run parity/property/fuzz/benchmark and artifact verification commands.
5. Keep native disabled when parity, input bounds or scheduler safety is uncertain.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

Fallback is active and correct, native errors stop, and the system stays inside the declared capacity envelope.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
