# ADR 0300: Rust runtime foundation V0

- Status: Accepted
- Date: 2026-07-17
- Owner: Senior 3

## Context

LIQI Platform V0 needs one reproducible Rust foundation for an HTTP API, a realtime gateway, and an at-least-once worker. The initial host is one OCI A1 Flex node with 4 OCPU and 24 GB RAM. PostgreSQL is the sole durable authority. V0 must stay simple enough to operate and recover on one node while preserving seams for later node separation.

## Decision

- Pin Rust `1.97.1`, the current stable patch release that fixes the LLVM miscompilation reported for `1.97.0`.
- Use a Cargo workspace with Rust 2024 edition and resolver 3.
- Use Tokio for async runtime and cancellation, Axum/Tower for HTTP and WebSocket transport, Serde for wire/config serialization, `tracing` for structured instrumentation, and OpenTelemetry-compatible trace context.
- Produce three deployable artifacts: `liqi-api`, `liqi-realtime`, and `liqi-worker`.
- Keep configuration, protocol, telemetry, application ports, and test support in focused shared crates. Do not create a generic framework or dynamic plugin system.
- Every network body, queue, pool, concurrency gate, retry, timeout, and shutdown drain has a configured bound.
- Binaries accept `--config <path>` and the compatibility alias `LIQI_CONFIG_PATH`. Runtime paths and host wiring remain external contracts.
- Liveness reports process/event-loop health only. Readiness fails when required persistence or migration capability is unavailable.
- Application shutdown first marks readiness as draining, stops accepting new work, cancels child work, and drains until the configured deadline.

## Resource defaults

Defaults are deliberately below the 4 OCPU/24 GB host envelope:

- API in-flight requests: 256.
- Realtime outbound queue: 128 messages per connection.
- Worker concurrency: 8 and claim batch: 32.
- Database pool: 16 per service example; production aggregate must be reconciled with PgBouncer and Senior 2.
- Blocking tasks: 4 per process.
- Request body: 1 MiB; realtime message: 64 KiB.

These are safe starting bounds, not capacity claims. Host-level hard memory limits belong to Senior 4 operations contracts.

## Consequences

- Runtime processes can be split onto separate nodes without changing public contracts.
- Axum intentionally couples the transport layer to Tokio/Tower; this is acceptable because runtime independence has no V0 value.
- The fake persistence provider is dev/test only and cannot satisfy production readiness.
- OTLP export is capability-gated until the final telemetry semantic contract from Senior 4 is consumed; JSON logs and trace/request correlation exist from checkpoint 1.

## References

- Rust 1.97.1 release: https://blog.rust-lang.org/2026/07/16/Rust-1.97.1/
- Axum 0.8 documentation: https://docs.rs/axum/0.8.9/axum/
- Tokio 1.52 documentation: https://docs.rs/tokio/1.52.3/tokio/
- OpenTelemetry Rust: https://docs.rs/opentelemetry/0.32.0/opentelemetry/
