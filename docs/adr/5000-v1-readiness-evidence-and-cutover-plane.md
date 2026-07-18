# ADR 5000: V1 readiness evidence and cutover plane

- Status: accepted for provider integration
- Date: 2026-07-18
- Decision owner: Senior 5
- Affected consumers: Senior 1, Senior 2, Senior 3, Senior 4

## Context

V0 contains a useful fail-closed operations control plane, but its contracts identify a Rust runtime and V0 release topology. Reinterpreting those schemas for the BEAM-native V1 would silently change observable semantics and weaken the retained V0 rollback target.

V1 also adds evidence that V0 did not require as one coherent bundle: 2,000-session load floor, reconnect storm, BEAM scheduler/mailbox capacity, Rustler fallback, isolated restore/PITR, phased traffic cutover and a post-cutover observation window.

## Invalid assumptions

1. V0 runtime evidence can represent a Phoenix/OTP/Rustler release without versioning.
2. A successful health probe is sufficient production-readiness evidence.
3. Missing provider commands can be replaced by readiness-owned wrappers or fixtures.
4. A single-node activation can be described as HA, zero-downtime or automatic failover.
5. Evidence can be reused across Git SHAs or release IDs.

## Decision

V1 uses additive Draft 2020-12 JSON Schemas under `contracts/readiness/**` and a new SLO/alert contract under `contracts/operations/**`. V0 contracts remain unchanged for the rollback window.

The control plane has three strict layers:

1. `validate_readiness_v1.py` validates source contracts, exact catalogs, referenced runbooks and forbidden mutation/fallback patterns.
2. `run_provider_gates_v1.py` invokes only provider-published commands. A missing command remains owner-attributed `blocked`; it is never emulated.
3. `compose_readiness_v1.py` validates hashes, freshness, exact Git SHA/release binding, live evidence mode, checkpoint status, compatibility and rollback evidence before issuing the sole final verdict.

Load generation uses k6 HTTP and the current `k6/websockets` API. The harness owns workload shape and measurements, not Phoenix/realtime semantics. Provider-owned endpoints, authentication, payloads and resume protocol remain external inputs.

All live and mutation-sensitive workflows are manual and environment-protected. Source CI never runs `tofu apply`, OCI mutation, live deployment, live migration, restore, traffic switch or rollback.

## Boundary and contract impact

- Senior 1 publishes BEAM source/integration/artifact validation and a live platform probe.
- Senior 2 publishes database source/integration evidence and approved isolated restore/PITR evidence.
- Senior 3 publishes native safety, benchmark/fuzz/parity and ARM64 artifact identity evidence.
- Senior 4 publishes infrastructure source/plan, host readiness and rollback evidence; Senior 4 remains the only OCI/traffic mutation executor.
- Senior 5 publishes readiness contracts, load/resilience/recovery acceptance, evidence composition and final verdict.

Provider outputs must be directly consumable at the registered seam. Provider failures return to the provider owner; Senior 5 does not modify provider internals.

## Capacity decision

A passed capacity result must retain at least 1 OCPU, 4 GiB memory and 20 GiB disk, with provider totals at or below 3 OCPU, 20 GiB and 180 GiB. It must declare BEAM schedulers, dirty schedulers, async threads, PostgreSQL/PgBouncer/Ecto/Oban limits, native memory/concurrency, WebSocket memory/queues, actor mailbox/ETS limits and telemetry bounds.

No swap capacity is credited. Simultaneous V0/V1 runtime is accepted only when the aggregated envelope still passes; otherwise cutover uses short overlap, drain, health-gated route switch and resume-aware reconnect.

## Compatibility and migration

- Compatibility: additive.
- V0 schemas and release artifacts remain valid during the rollback window.
- V1 database migrations must be expand-compatible and readable by the retained rollback target.
- Realtime protocol negotiation, versioned configuration and native fallback are required evidence.
- Evidence is immutable by SHA-256 and cannot be reused for a different SHA/release.

## Temporary implementation

`operations/readiness/provider-gates-v1.json` records each seam as `pending-provider-publication`, `pending-integration`, `available` or `pending-live-evidence`. Exact provider branch/commit metadata is required once a consumer-ready command has been published.

- Owner: the senior named by each registry entry.
- Removal condition: replace `pending-integration` with `available` only after the exact provider commit is integrated, required paths exist on the resulting SHA and provider/consumer contract tests pass. A live collector may then be `pending-live-evidence` until its approved exact-release result exists.
- None of the pending states is a runtime fallback or production-ready evidence.

## Trade-offs

The additive schemas and explicit evidence bundle add files and validation work. They avoid more expensive ambiguity during migration, preserve rollback semantics, keep provider ownership visible and allow future node separation without changing durable or wire semantics.

k6 is an additional test dependency, but it supports HTTP/WebSocket load, thresholds and machine-readable summaries without introducing a production runtime dependency. The harness remains replaceable because evidence contracts, not k6 internals, are the stable seam.

## Operational consequences

- `blocked` is not `passed`.
- Synthetic/fixture evidence cannot produce the final pass verdict.
- No cutover pass is possible without a passed rollback result and at least a 30-minute observation window.
- Security/correctness events have zero budget and immediately block promotion/cutover.
- Single-node reboot or replacement is an outage and must not be marketed as HA.

## Primary references

- JSON Schema Draft 2020-12: https://json-schema.org/draft/2020-12
- GitHub Actions workflow syntax and permissions: https://docs.github.com/actions/reference/workflows-and-actions/workflow-syntax
- GitHub deployment environments and protection rules: https://docs.github.com/actions/reference/workflows-and-actions/deployments-and-environments
- Grafana k6 WebSockets: https://grafana.com/docs/k6/latest/javascript-api/k6-websockets/
- Grafana k6 thresholds: https://grafana.com/docs/k6/latest/using-k6/thresholds/
- Erlang efficiency guide: https://www.erlang.org/doc/system/eff_guide.html
