# V1 load and reconnect harness

The k6 scripts own workload shape and raw measurement only. Phoenix endpoints, authentication, command/event payloads, subscription messages and resume protocol are provider-owned inputs. The scripts fail when those inputs are absent rather than inventing semantics.

## Floor workload

```bash
LIQI_BASE_URL=https://staging.example.invalid \
LIQI_WS_URL=wss://staging.example.invalid/socket \
LIQI_RELEASE_ID=liqi-v1-<release> \
LIQI_COMMAND_BODY_JSON='<provider-owned-json>' \
LIQI_EVENT_BODY_JSON='<provider-owned-json>' \
LIQI_WS_HELLO_JSON='<provider-owned-json>' \
LIQI_WS_SUBSCRIBE_JSON='<provider-owned-json>' \
k6 run tests/load/v1-floor.js
```

Defaults encode the acceptance floor: 2,000 WebSockets, 200 active subscriptions, 50 durable commands/s, 500 realtime events/s and a 30-minute steady interval. The resulting k6 summary is raw evidence and must be combined with host/BEAM/database/native telemetry into `load-result-v1`; it is not a production readiness result by itself.

## Reconnect storm

```bash
LIQI_WS_URL=wss://staging.example.invalid/socket \
LIQI_RELEASE_ID=liqi-v1-<release> \
LIQI_WS_INITIAL_JSON='<provider-owned-json with {{VU_ID}}>' \
LIQI_WS_RESUME_JSON='<provider-owned-json with {{VU_ID}}>' \
LIQI_RESUME_SUCCESS_FIELD='<provider-owned-response-field>' \
k6 run tests/load/reconnect-storm-v1.js
```

The scenario builds 2,000 baseline sessions and reconnects 500 of them over at most 60 seconds. A passed `reconnect-storm-v1` additionally requires provider telemetry proving resume/gap-repair semantics, zero durable event loss and command-plane recovery.
