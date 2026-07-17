# ADR 0408: Bounded telemetry and local log retention

## Status

Accepted for V0.

## Decision

Use the OpenTelemetry Collector Contrib distribution as a bounded relay for application OTLP and host metrics. The release must pin the collector image by digest. V0 accepts OTLP only on loopback and exports through an approved OTLP/HTTP sink reference whose endpoint and authorization are environment variables, never values committed to Git.

The memory limiter is first in every pipeline. Export queue, retry duration, batch size and message size are finite. Tail sampling is disabled on the single node. Private and high-cardinality attributes are deleted as a runtime safety net; Senior 3 remains responsible for not emitting them.

Structured local service logs remain in persistent journald with 2 GiB maximum use, 10 GiB keep-free, seven-day maximum retention and rate limiting. The remote telemetry sink is not a source of truth. Sink outage uses bounded retry and may drop noncritical remote telemetry while local journal evidence remains available.

## Rationale

The official Collector components provide memory limiting, batching, host metrics, attribute deletion, bounded sending queues and retry windows. systemd-journald provides synchronous disk-use and retention controls. These are adopted rather than reimplemented.

## Consequences

- Missing required release, recovery or security signals blocks promotion.
- Full observability SaaS and a local metrics/traces database remain outside V0.
- Senior 1 installs the reviewed journald/collector runtime configuration; Senior 3 emits the required telemetry semantics.
- Increasing queue, memory or retention budgets requires a capacity decision note.
