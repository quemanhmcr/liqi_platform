# LIQI Platform

Self-owned backend platform for LIQI on Oracle Cloud Infrastructure. V0 establishes a deployable, observable and recoverable foundation; it does not implement the LIQI business engine.

## Repository boundary

- `liqi_match`: mobile client and client-side adapters.
- `liqi_platform`: Rust services, PostgreSQL authority, OCI infrastructure source, security controls and operations control plane.
- OCI CLI configuration, PEM files, tokens, database passwords and signing keys remain outside Git.

## V0 runtime envelope

```text
OCI VM.Standard.A1.Flex — 4 OCPU / 24 GiB RAM
├── PostgreSQL authority
├── PgBouncer
├── Rust API
├── Rust realtime gateway
├── Rust worker
├── TLS/reverse proxy edge
├── OpenTelemetry Collector
└── host observability
```

PostgreSQL is the only durable authority. V0 is a health-gated single-node replacement/restart design, not HA or zero-downtime canary. At least 1 OCPU and 4 GiB remain reserved; declared hard limits cannot exceed 3 OCPU, 20 GiB RAM or the 200 GiB combined disk envelope.

## Operational golden path

```text
clean source
→ operations/provider contract validation
→ deterministic release manifest
→ deployment specification and preflight
→ liveness + readiness + platform-probe health gate
→ activation or bounded rollback
→ telemetry, SLO and recovery freshness evidence
```

Provider logic is never reproduced in CI. Missing provider seams produce an owner-attributed `blocked` integration result. Source CI may tolerate blocked seams during the V0 checkpoint grace period; manual integration and promotion gates are strict and contain no mock fallback.

## Senior 4 validation

Install the pinned Python control-plane dependencies:

```bash
python -m pip install -r operations/ci/requirements-v0.txt
```

Run source controls:

```bash
python scripts/operations/validate_contracts.py
python scripts/operations/validate_provider_registry.py --allow-pending
python scripts/operations/validate_dependency_policy.py
python scripts/operations/validate_operability_catalog.py
python scripts/operations/validate_ci_workflows.py
python scripts/operations/scan_repository_secrets.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Run provider source gates without pretending missing branches are available:

```bash
python scripts/operations/run_provider_gates.py \
  --stage source \
  --environment development \
  --output .artifacts/provider-source-result.json \
  --allow-blocked
```

The manual GitHub provider integration workflow performs no OCI apply, host activation or production deployment. Paid/unknown resources, OCI mutations and build/release execution require explicit project-owner approval.

## Build boundary

Senior 3 owns the Cargo workspace, artifact names and exact runtime build semantics. Senior 4 prepares deterministic release and CI source but does not run build/prebuild or invent missing provider commands. Build commands will be published only after the runtime provider contract is merged and reviewed.

See `CONTRIBUTING.md`, `operations/release/`, `operations/deployment/` and `operations/runbooks/` for governance and procedures.
