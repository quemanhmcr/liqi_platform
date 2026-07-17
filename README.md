# LIQI Platform

Self-owned backend platform for LIQI on Oracle Cloud Infrastructure. V0 establishes a deployable, observable and recoverable foundation; it does not implement the LIQI business engine.

## Repository boundary

- `liqi_match`: mobile client and client-side adapters.
- `liqi_platform`: Rust services, PostgreSQL authority, OCI infrastructure source, security controls and operations control plane.
- OCI CLI configuration, PEM files, tokens, database passwords and signing keys remain outside Git.

## V0 runtime envelope

```text
OCI VM.Standard.A1.Flex â€” 4 OCPU / 24 GiB RAM
â”œâ”€â”€ PostgreSQL authority
â”œâ”€â”€ PgBouncer
â”œâ”€â”€ Rust API
â”œâ”€â”€ Rust realtime gateway
â”œâ”€â”€ Rust worker
â”œâ”€â”€ TLS/reverse proxy edge
â”œâ”€â”€ OpenTelemetry Collector
â””â”€â”€ host observability
```

PostgreSQL is the only durable authority. V0 is a health-gated single-node replacement/restart design, not HA or zero-downtime canary. At least 1 OCPU and 4 GiB remain reserved; declared hard limits cannot exceed 3 OCPU, 20 GiB RAM or the 200 GiB combined disk envelope.

## Operational golden path

```text
clean source
â†’ operations/provider contract validation
â†’ deterministic release manifest
â†’ deployment specification and preflight
â†’ liveness + readiness + platform-probe health gate
â†’ activation or bounded rollback
â†’ telemetry, SLO and recovery freshness evidence
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
python scripts/operations/validate_telemetry_runtime.py
python scripts/operations/validate_provider_compatibility.py --output .artifacts/provider-compatibility.json --allow-missing
python scripts/operations/collect_provider_capacity.py --output .artifacts/provider-capacity.json --allow-blocked
python scripts/operations/assemble_source_readiness.py --provider-result .artifacts/provider-source-result.json --compatibility-result .artifacts/provider-compatibility-result.json --capacity-result .artifacts/provider-capacity-result.json --output .artifacts/source-integration-readiness-v0.json --allow-blocked
python scripts/operations/validate_ci_workflows.py
python scripts/operations/scan_repository_secrets.py
python scripts/release/validate_supply_chain_evidence.py --manifest <manifest.json> --sbom <release.spdx.json> --provenance <release.intoto.jsonl> --output <result.json>
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

The promotion form requires both `oci_plan_run_id` and `database_recovery_run_id`. The OCI artifact contains exactly one `oci-plan.json` produced by `tofu show -json`. The database artifact follows `operations/integration/database-recovery-evidence-v0.md`. Senior 4 downloads and validates both but never creates/applies the plan or performs backup/restore.

Host activation remains an owner-run command. First run `scripts/release/activate_release.py` without `--execute`; execution requires the reviewed deployment-spec SHA-256 and approval reference. Recovery exercises follow the same dry-run-first rule through `scripts/operations/run_recovery_exercise.py`.

Current integration blockers are machine-readable: Senior 1 and Senior 3 have not published provider capacity budgets; Senior 3 has not published the runtime seam; Senior 1 journald policy differs from the operations contract; and Senior 2 restore command ownership conflicts with `operations/**`. Senior 2 recovery status is now a published provider seam. No Senior 4 fallback is provided.

## Build boundary

Senior 3 owns the Cargo workspace, artifact names and exact runtime build semantics. Senior 4 prepares deterministic release and CI source but does not run build/prebuild or invent missing provider commands. Build commands will be published only after the runtime provider contract is merged and reviewed.

See `CONTRIBUTING.md`, `operations/release/`, `operations/deployment/` and `operations/runbooks/` for governance and procedures.

## Integration order

The low-conflict provider merge sequence and required pre-merge corrections are versioned in `operations/integration/merge-plan-v0.md`. Source readiness is authoritative; `blocked` means a seam is not merged, while `failed` means a present seam must be repaired by its owner.
