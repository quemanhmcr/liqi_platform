# Platform probe V0 integration specification

This probe is an operational walking skeleton only. It is not a LIQI domain abstraction and must not be reused for player, social, match, session, conversation, or trust semantics.

## Accepted flow

1. `POST /platform/v0/probes` receives a bounded JSON request containing `clientProbeId`.
2. The API uses `clientProbeId` as the durable probe ID and derives a deterministic UUIDv5 event ID.
3. `platform.request_probe_v0` atomically inserts the probe and `platform.probe.requested.v0` outbox event.
4. The API returns `202` only after the stored function commits.
5. The worker claims through `platform.claim_outbox_v0` with consumer `liqi-platform-probe-worker-v0`, batch no larger than 50, and a 30-second lease.
6. The worker calls `platform.apply_probe_effect_and_ack_v0`; terminal effect and acknowledgement commit atomically.
7. Duplicate delivery returns `already_succeeded` and cannot create a second terminal effect.
8. A realtime provider may publish the committed event only through the approved committed-handoff interface. It may not claim the worker outbox or read authority tables directly.

## Current checkpoint status

- API durable commit: implemented by Senior 3 adapter against Senior 2 migration version 2.
- Worker claim/effect/ack: implemented by Senior 3 adapter against Senior 2 migration version 2.
- Realtime publication: intentionally not ready; Senior 2 has not yet published the committed-handoff function.
- Dev/test orchestration: fake provider exists only behind `dev-fakes` plus `persistence.fake=true` and is rejected in staging/production.

## Provider validation after merge

```bash
python database/tests/contract/validate_wire_mapping.py \
  contracts/events/examples/platform-probe-requested-v0.json
```

The full disposable-PostgreSQL gate remains owned by Senior 2:

```bash
LIQI_TEST_DATABASE=liqi_v0_test \
  database/tests/integration/run_database_tests.sh
```
