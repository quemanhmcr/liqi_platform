# Security and correctness event V1

## Trigger
- Any authorization bypass, secret exposure, duplicate durable identity, event-before-commit or durable event loss counter is above zero

## Expected degradation and safety

This is outside the availability error budget. Cutover/promotion stops immediately and the affected route is rolled back or isolated.

## Operator procedure
1. Stop traffic expansion and preserve logs/evidence with redaction.
2. Record exact release ID, Git SHA, request/event IDs, timestamps and affected authority records.
3. Rotate or revoke exposed credentials only through the approved owner procedure.
4. Attribute the failing seam to the owning senior; do not add a permissive fallback.
5. Require a provider fix, regression test and new exact-release evidence before resuming.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

The incident is resolved, all five counters are zero for a full observation window, and replacement evidence is bound to the fixed SHA/release.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
