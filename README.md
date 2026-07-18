# LIQI Platform

`liqi_platform` is the backend platform for LIQI on Oracle Cloud Infrastructure. PostgreSQL is the durable authority; runtime process state, PubSub/Presence, native kernels and workers are rebuildable coordination or acceleration layers.

## Runtime generations

- **V0 rollback target:** Rust runtime and its versioned V0 operations contracts remain retained during the V1 migration window.
- **V1 target:** Phoenix HTTP/WebSocket â†’ Elixir/OTP actors â†’ Ecto/Postgrex â†’ PostgreSQL/outbox/Oban â†’ bounded Rustler kernels or isolated Rust processes.

V1 is a production-shaped single-node deployment on `VM.Standard.A1.Flex` with 4 OCPU, 24 GiB RAM and at most 200 GiB combined storage. It is not multi-AZ HA, active-active, automatic failover, zero-downtime or exactly-once end-to-end.

Mandatory reserve:

```text
Host:       4 OCPU / 24 GiB / 200 GiB
Providers: â‰¤3 OCPU / 20 GiB / 180 GiB
Reserve:   â‰¥1 OCPU /  4 GiB /  20 GiB
```

Swap is not counted as capacity.

## V1 readiness control plane

Senior 5 owns the fail-closed control plane under:

```text
.github/**
operations/**
contracts/operations/**
contracts/readiness/**
tests/load/**
tests/resilience/**
tests/recovery/**
tests/live/**
docs/adr/50xx-*.md
```

The control plane validates provider output; it does not reproduce Phoenix, database, native or infrastructure logic.

```text
provider evidence
â†’ exact SHA/release compatibility
â†’ capacity
â†’ load/reconnect/resilience
â†’ restore and rollback
â†’ security and mutation approvals
â†’ phased cutover/post-cutover observation
â†’ one final verdict
```

The only passing final verdict is:

```text
V1 PRODUCTION-SHAPED ON OCI
```

Any missing, blocked, stale, synthetic, release-mismatched or failed required evidence produces:

```text
V1 NOT READY
```

## Source validation

Install the pinned control-plane dependency set:

```bash
python -m pip install -r operations/ci/requirements-v0.txt
```

Run V1 source gates:

```bash
python operations/bin/validate_readiness_v1.py
python -m unittest discover -s tests/live -p 'test_*.py' -v
python -m unittest discover -s tests/recovery -p 'test_*.py' -v
python -m unittest discover -s tests/resilience -p 'test_*.py' -v
node --check tests/load/v1-floor.js
node --check tests/load/reconnect-storm-v1.js
python scripts/operations/validate_ci_workflows.py
python scripts/operations/scan_repository_secrets.py
```

Collect the strict source checkpoint for the integrated provider graph:

```bash
SHA="$(git rev-parse HEAD)"
RELEASE_ID="liqi-v1-source-${SHA:0:12}"
python operations/bin/run_provider_gates_v1.py \
  --stage source \
  --release-id "$RELEASE_ID" \
  --output .artifacts/v1/checkpoints/source.json
```

The source checkpoint is strict after provider integration. Later artifact/live checkpoints remain fail-closed until exact-release evidence exists.

## Load and recovery

`tests/load/v1-floor.js` encodes the A1 acceptance floor: 2,000 concurrent WebSockets, 200 active subscriptions, 50 durable commands/s, 500 realtime events/s, 25% reconnect within 60 seconds and a 30-minute steady interval. Raw k6 output is not sufficient; `load-result-v1` also requires BEAM, mailbox, ETS, database, outbox, Oban, native, CPU, memory, disk and post-load recovery measurements.

Restore evidence must come from an approved provider-owned isolated restore/PITR drill. Restoring over the live database is forbidden. A backup claim is not accepted until restore, migration/invariant checks, read-only Elixir probe and cleanup pass.

## Protected live flow

`.github/workflows/v1-live-readiness.yml` is manual-only and uses protected GitHub environments. It can run read-only probes and, only with an explicit approval reference, the provider-owned isolated restore command. It contains no OCI apply, live deployment, traffic switch or rollback mutation.

Senior 4 remains the executor for OCI and traffic mutations. The final composer consumes the approved mutation log rather than performing those changes:

```bash
python operations/bin/compose_readiness_v1.py \
  --git-sha <40-char-sha> \
  --release-id <liqi-v1-release-id> \
  --environment production \
  --evidence capacity=<path> \
  --evidence platform-probe=<path> \
  --evidence load=<path> \
  --evidence reconnect=<path> \
  --evidence recovery=<path> \
  --evidence resilience=<path> \
  --evidence security=<path> \
  --evidence cutover=<path> \
  --evidence rollback=<path> \
  --checkpoint source=<path> \
  --checkpoint integration=<path> \
  --checkpoint artifact=<path> \
  --checkpoint live-staging=<path> \
  --checkpoint promotion=<path> \
  --checkpoint cutover=<path> \
  --checkpoint post-cutover=<path> \
  --compatibility <path> \
  --oci-mutations <path> \
  --output .artifacts/v1/v1-readiness-result.json
```

The canonical artifact layout is documented in `operations/readiness/evidence-bundle-layout-v1.md`. Operational response is under `operations/runbooks/`; the design decision and provider removal conditions are in `docs/adr/5000-v1-readiness-evidence-and-cutover-plane.md`.

## Security boundary

Never commit OCI configuration, PEM/private keys, tokens, session tokens, database passwords, backup contents, Terraform/OpenTofu state or unredacted crash dumps. Evidence stores references and checksums, not credentials or durable data copies.
