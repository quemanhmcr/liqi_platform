# Disk pressure V1

## Trigger
- disk exhaustion forecast is seven days or less
- Free disk approaches the mandatory 20 GiB reserve

## Expected degradation and safety

Artifact/log growth and optional producers stop before PostgreSQL, WAL or recovery paths lose capacity.

## Operator procedure
1. Freeze promotion and traffic expansion.
2. Capture filesystem usage by database/WAL, releases, telemetry, logs and temporary files.
3. Apply only documented retention; never delete authoritative database/WAL/backup evidence to clear a gate.
4. Ask Senior 4 to inspect host storage and systemd/Caddy/release retention seams.
5. Rollback or drain when the 20 GiB reserve cannot be protected.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

At least 20 GiB reserve and a greater-than-seven-day forecast are restored without deleting required evidence or durable authority.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
