# Telemetry semantic conventions V0

Owner: Senior 4. Instrumentation implementation owner: Senior 3 for Rust services, Senior 1 for host signals, Senior 2 for database/recovery signals.

## Required resource identity

Every signal must carry or be joinable to:

- `service.namespace=liqi-platform`
- stable `service.name`
- artifact-derived `service.version`
- `deployment.environment.name`
- `liqi.release.id`

`service.instance.id` is required at runtime but is intentionally not a metric label unless a diagnostic dashboard explicitly opts in. Release ID must be visible on health/probe responses and telemetry.

## Request and operation identity

- Logs and spans may contain `trace_id` and `request_id`.
- Metrics must not use trace ID, request ID, actor, player, conversation, session, email or IP as labels.
- HTTP metrics use normalized route templates, never raw URLs or path identifiers.
- Result classes are bounded: `success`, `client_error`, `server_error`, `rejected`, `timeout`, `cancelled`, `degraded`.
- Error classes are bounded stable codes, not exception text.

## Latency

Latency uses histograms and percentile queries. Average latency is not a release or SLO gate. HTTP server duration follows `http.server.request.duration` in seconds with the OpenTelemetry advisory buckets defined in the telemetry fixture. Realtime committed-to-delivered latency is measured from durable commit timestamp to acknowledged delivery, not from socket write start.

## Required platform signals

The integrated platform must expose:

- API availability, durable write success and p95/p99 server latency.
- Realtime active connections, delivery duration, rejected subscriptions and slow-consumer disconnects.
- Worker queue depth, retry count, terminal failure count and processing duration.
- Database pool used/max, saturation, wait duration, outbox oldest age and dispatch lag.
- Host CPU, memory, disk usage, disk exhaustion forecast and process restart count.
- Backup age, WAL archive lag and restore verification timestamp.

## Redaction

Structured logging must redact authorization, cookies, passwords, tokens, secrets, private keys, private message bodies and signed media URLs before serialization. Redaction failure is fail-closed: drop the unsafe field or event rather than emit plaintext.

## Sampling and retention

- Security and correctness events are never probabilistically sampled.
- Traces use bounded parent-based sampling outside development.
- Debug logs are disabled by default in production.
- Retention is specified per signal class in operations policy; queues and local disk buffers are bounded.
