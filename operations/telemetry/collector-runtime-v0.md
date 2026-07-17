# OpenTelemetry Collector runtime V0

The collector is a bounded relay, not a durable authority.

- Application OTLP and collector health endpoints bind to loopback.
- Host metrics use the official `host_metrics` receiver at 15-second cadence.
- `memory_limiter` is first in every pipeline.
- Private/high-cardinality attributes are deleted before export.
- Export queue and retry window are finite. When the sink remains unavailable, noncritical remote telemetry may be dropped; local structured service logs remain in journald.
- Tail sampling is disabled because it retains trace state in memory and is unnecessary for the V0 envelope.
- The collector image must be pinned by digest and its version recorded in release provenance.
- Sink endpoint and authorization are environment references materialized outside Git.

`generate_otel_collector_config.py` creates source configuration only. It does not install or start the collector and does not provision a telemetry backend.
