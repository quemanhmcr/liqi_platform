# ADR 1003: V1 live platform probe and bounded Phoenix wire client

- Status: Accepted for source integration; live evidence pending
- Date: 2026-07-18
- Decision owner: Senior 1
- Consumers: Senior 2, Senior 3, Senior 4, Senior 5

## Context

Senior 5 requires one provider-owned live command that proves HTTPS, runtime readiness, Phoenix WebSocket authorization, durable command identity, committed handoff, realtime delivery, ACK/resume/gap repair, worker terminal effect, native execution and native fallback. Reimplementing runtime semantics in the readiness control plane would violate provider ownership.

The exact locked Phoenix 1.8 serializer encodes V2 JSON frames as `[join_ref, ref, topic, event, payload]`. The live command must therefore consume this wire directly, but it must not add a long-lived agent, external WebSocket dependency or unbounded receive loop.

## Decision

1. Publish `beam/bin/platform-probe` as a read-only executable backed by Python standard-library HTTPS, TLS and RFC 6455 framing.
2. Require an origin-only `https://` base URL. Redirects, URL credentials, paths, query strings, plaintext HTTP and disabled certificate verification are forbidden.
3. Read the operator credential from `LIQI_PROBE_AUTH_TOKEN_REF` (`file://`, `systemd-credential://` or `env://`) with `LIQI_PROBE_AUTH_TOKEN` as a compatibility fallback; never accept the value in argv or query parameters and never include it in evidence or errors.
4. Bound every HTTPS/WebSocket step, response body, frame, fragmented message and pending Phoenix message buffer. The outer readiness runner retains its 900-second hard timeout; the provider command has no background process.
5. Test unauthorized HTTP command and WebSocket rejection before authorized join. The channel remains restricted to `platform-probe:<uuid>` actor keys.
6. Submit an idempotent duplicate durable command and require one stable event ID. Observe realtime sequence, ACK, terminal worker effect and a second event recovered after disconnect through the durable resume cursor.
7. Validate the configured native kernel against the pure Elixir reference. Separately exercise the provider's optional unavailable-capability fallback path; this negative-path diagnostic is not a production command adapter and owns no durable state.
8. Emit exactly the eleven checks defined by `live-platform-probe-v1`. Missing credentials/providers/artifacts remain `blocked`; observed semantic/security mismatches are `failed`. Synthetic unit tests may validate orchestration but can never produce promotion evidence.

## Failure and cleanup

Sockets use per-step deadlines and are closed on normal completion; process exit closes any socket after an exceptional path. No daemon, task queue or retry loop survives the command. HTTP polling is bounded to the terminal-effect window and does not mutate infrastructure.

## Provider actions

- Senior 2 must publish callable migration-8 command, handoff and terminal-observation functions before durable/worker checks can pass.
- Senior 3 must provide the verified ARM64 NIF for `native-kernel`; the deterministic fallback check does not substitute for artifact evidence.
- Senior 4 must preserve `x-liqi-probe-token` through Caddy without logging its value and materialize the token as a protected credential file.
- Senior 5 must add `LIQI_PROBE_AUTH_TOKEN_REF` to the provider-gate required environment (preferred; `LIQI_PROBE_AUTH_TOKEN` is a one-window adapter) and register the exact provider commit.

## Compatibility

The command is additive and protocol-v1 only. V0 rollback traffic is not probed through this command. No durable dual write is introduced.

## Validation

```bash
python -m unittest discover -s beam/tests -p 'test_*.py' -v
env -u LIQI_PROBE_AUTH_TOKEN -u LIQI_PROBE_AUTH_TOKEN_REF beam/bin/platform-probe \
  --base-url https://probe.example.test \
  --release-id liqi-v1-test-release \
  --output .artifacts/live-probe-missing-token.json
```

## OCI impact

None. The command is source-only until Senior 4 performs an explicitly approved deployment and Senior 5 authorizes live read-only evidence collection.
