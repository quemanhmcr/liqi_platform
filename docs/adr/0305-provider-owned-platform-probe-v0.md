# ADR 0305: Provider-owned platform probe V0

## Status

Accepted for Senior 3 provider publication.

## Context

`POST /platform/v0/probes` proves only that the API committed a platform probe and its durable outbox event atomically. Promotion requires evidence that the deployed release is observable, the worker applied the terminal effect idempotently, the outbox reached terminal success and a subscribed realtime client received the committed event.

Senior 4 owns the `platform-probe-result-v0` schema and evidence orchestration. Senior 3 must provide the executable seam directly; Senior 4 must not infer binary flags, inspect runtime internals or synthesize missing checks.

Senior 2 has not yet published a committed realtime handoff function. The realtime role is intentionally prohibited from claiming the durable outbox or reading authority tables directly. Production realtime readiness therefore remains fail closed.

## Decision

The stable provider command is:

```text
liqi-platform-tool platform-probe --output <path>
```

It consumes only environment references:

- `LIQI_TEST_DATABASE`: disposable probe database connection; never logged or written to evidence;
- `LIQI_RELEASE_ID`: expected deployed release identity;
- `LIQI_ENVIRONMENT`: `development`, `staging` or `production`;
- `LIQI_PROBE_AUTH_TOKEN`: required outside development once the authentication provider exists.

The runner:

1. verifies API, realtime and worker health plus artifact release/environment identity over loopback;
2. establishes and subscribes a bounded realtime connection before issuing the command;
3. commits a unique probe through the public API;
4. observes the worker terminal effect and successful outbox terminal state in the disposable database;
5. waits for the same committed event ID on the realtime subscription;
6. writes `platform-probe-result-v0` atomically and returns non-zero when any check fails.

HTTP and WebSocket clients use mature Rust libraries. Redirects are disabled. Endpoint parsing requires numeric loopback authority with an explicit port and rejects credentials, query/fragment tricks and external hosts. Per-step timeout is bounded to 15 seconds so the complete eight-check runner remains below Senior 4's 300-second gate.

All evidence references are opaque identifiers. Transport, SQL and internal error details do not leave the process. A result missing any required check is completed with explicit `probe.incomplete` failures; an empty set can never pass.

## Temporary database observation boundary

V0 terminal evidence reads the disposable test database tables introduced by Senior 2 migration `000000000002_platform_outbox_probe.sql`. This is limited to the promotion probe and is not an application/runtime repository contract.

Senior 2 should publish a provider-owned probe observation function or approved read view. Senior 3 owns removing the direct disposable-test query when that seam becomes available. Runtime API/realtime/worker binaries do not gain direct-table privileges from this decision.

## Realtime dependency

Until Senior 2 publishes committed realtime handoff and Senior 3 integrates it:

- `realtime-readiness` remains failed;
- realtime delivery cannot be reported passed;
- the result remains schema-valid with `status=failed`;
- promotion remains fail closed.

No fake delivery, outbox claim by realtime or synthetic success adapter is permitted.

## Compatibility

The CLI command and validation-manifest fields are additive. Existing runtime flags and wire versions do not change. The result schema remains owned by Senior 4.
