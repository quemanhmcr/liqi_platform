# Capacity dashboard requirements V0

The dashboard must show hard limit, current use, forecast and owner for:

- Host CPU and memory, including reserved 1 OCPU/4 GiB headroom.
- Filesystem used/free bytes, daily growth and days-to-exhaustion forecast.
- PostgreSQL memory/disk budget, server connections, reserved connections and PgBouncer waiting clients.
- API active requests, bounded concurrency and database checkout wait.
- Realtime active connections, per-connection memory estimate, outbound queue occupancy and slow-consumer disconnects.
- Worker concurrency, queue depth, oldest work age, retry budget and terminal failures.
- OpenTelemetry Collector memory, queue fill, dropped telemetry and local buffer disk.
- Release ID, environment and capacity contract version.

No dashboard panel may group by user, player, conversation, session, raw URL, request ID, trace ID, email or IP.
