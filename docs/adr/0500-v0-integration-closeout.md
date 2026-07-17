# ADR 0500: V0 integration closeout and production-readiness checkpoint

- Status: Accepted for integrated source; release checkpoint blocked by owner-build evidence
- Date: 2026-07-17
- Decision owner: V0 integration closeout DRI
- Consumers: V1 platform/domain workstreams, project owner, infrastructure/database/runtime/operations providers

## Context

The four V0 provider branches were completed independently from baseline `2d72ce4`. Their source was individually valid, but shared seams were not yet compatible: host/operations versions and journald policy diverged; database recovery implementation violated ownership; runtime still used temporary persistence paths; capacity and PostgreSQL connections were not aggregated from all providers; source bytes and supply-chain fixture digests were not cross-platform deterministic; owner-build evidence was conflated with source failure; and the host did not yet materialize runtime base units or a fail-closed public edge.

V0 closeout must integrate providers directly. Operations may orchestrate providers but may not introduce a wrapper that hides missing or invalid provider output.

## Decision

### Merge order

The actual first-parent integration order was:

1. `v0/operability-release`
2. `v0/oci-secure-host`
3. host/operations compatibility repair
4. `v0/postgres-authority`
5. database recovery ownership repair
6. source-byte/checksum normalization
7. `v0/rust-runtime-skeleton`
8. runtime/database handoff and observation repair
9. capacity/readiness/host closeout repairs

Each provider history was preserved through a normal merge commit. No provider branch was force-deleted or rewritten.

### Canonical contract versions

- OCI host schema: `liqi.platform.oci-host/v0`
- OCI infrastructure output: `0.3.0`
- host bootstrap: `0.3.0`
- database contract: `database-v0`
- required database migration: `4`
- runtime configuration: `runtime-config-v0`; all API, realtime, and worker examples require migration `4`
- Rust toolchain: `1.97.1`, locked, AArch64 target published
- provider gate registry: `0.6.0`
- provider capacity registry: `0.3.0`
- owner build evidence: `owner-build-evidence-v0`

Infrastructure output and bootstrap are independent version fields and are both consumed by deployment/activation contracts. Bootstrap `0.3.0` materializes runtime provider base units and the infrastructure-owned fail-closed edge. OpenTofu gzip-compresses cloud-init and enforces the OCI 16 KiB encoded `user_data` limit.

### Host and operations compatibility

Operations consumes host output `0.3.0`. Journald has one materialized policy:

- `Storage=persistent`
- `SystemKeepFree=10G`
- `SystemMaxFileSize=256M`
- `RuntimeMaxUse=512M`
- `MaxFileSec=1day`
- `MaxRetentionSec=7day`
- `RateLimitIntervalSec=30s`
- `RateLimitBurst=10000`
- `ForwardToSyslog=no`

Runtime provider units are published under `services/systemd/**`, run as dedicated non-root identities, fail before start when config or per-service secret files are unavailable, and inherit the published runtime slice/capacity drop-ins. Cloud-init does not enable these units before release activation.

The infrastructure provider publishes a default NGINX edge that accepts only TCP 80/443 and rejects traffic until an approved site configuration is installed. Public DNS, certificate issuance, and approved-site activation are outside V0.

### Recovery ownership

Database-specific recovery implementation and provider-internal runbooks live under:

- `database/recovery/**`
- `database/runbooks/**`

The provider publishes real `prepare → restore → verify → cleanup` commands. Operations retains only orchestration, freshness policy, incident coordination, approval handling, and generic evidence composition. There is no operations wrapper pointing to the former path and no old-path promotion fallback.

### Runtime/database persistence seam

Migration `4` is the canonical provider boundary:

- `platform.database_readiness_v0(bigint)`
- `platform.read_realtime_handoff_v0(bigint, integer)`
- `platform.observe_probe_v0(uuid, uuid)`

Because migrations become immutable only after merge to `main`, migrations `2` and `4` were completed before the checkpoint to persist and project the full V0 envelope:

- `event_id`
- `event_type`
- `schema_version`
- `event_version`
- `occurred_at`
- `producer`
- `correlation_id`
- `causation_id`
- `aggregate_key`
- `ordering_key`
- `payload`
- `metadata`

Worker and realtime use one PostgreSQL row-to-envelope mapper. Realtime consumes committed handoff rows and reports readiness green only after a provider read succeeds. The platform probe observes terminal state only through `observe_probe_v0`; direct authority-table reads are forbidden by source validators. Durable effects remain idempotent and duplicate events do not create duplicate terminal effects.

### Capacity envelope

All totals are aggregated from the four provider budgets; tests do not hardcode replacement totals:

- host: 4 OCPU, 24 GiB memory
- reserve: 1 OCPU, 4 GiB memory, 20 GiB disk
- steady-state total: 1.51 OCPU
- hard total: 3.00 OCPU, 16,384 MiB memory, 160.2 GiB disk

Provider hard CPU and steady-state CPU limits are both 3 OCPU, preserving the one-OCPU host reserve.

PostgreSQL accounting separates logical clients from PostgreSQL server backends:

- runtime pooled demand: 35
- PgBouncer pooled server capacity: 40
- direct administrative/recovery reservation: 10
- total server reservation: 50
- PostgreSQL `max_connections`: 80
- reserved headroom: 30

### Source bytes and checksums

Root `.gitattributes` defines LF for source/contracts and binary treatment for exact-byte artifacts and key/certificate containers. Tracked source—not the validator—owns normalization. Migration manifests, release/SBOM/provenance fixtures, and dependent checksums are calculated from exact canonical tracked bytes.

A Windows checkout with `core.autocrlf=true` and an LF-only checkout must produce the same source-gate results. Production artifact hashes are never normalized by the validator.

### Readiness semantics

- `failed`: source/contract/evidence is present and invalid
- `blocked`: mandatory external evidence is absent
- `passed`: all evidence required by the stage is present and valid

Source CI runs all source providers. The four compile/link commands remain `pending-owner-build`; missing evidence is `blocked`, while wrong SHA, exact command, schema, or digest is `failed`. `owner-build-evidence-v0` binds command, log/result digest, approval reference, and exact Git SHA. Evidence is generated by the owner wrapper and is not edited by hand.

Promotion does not accept `blocked`. Database, recovery, plan, owner-build, activation, and platform-probe evidence must all be `passed` and bound to the promoted Git SHA/artifacts.

## Accepted V0 limitations

- single host; no multi-node HA or zero-downtime claim
- no production OCI plan/apply, deployment, DNS, TLS issuance, live database migration, live backup, or restore drill
- no runtime benchmark or load test
- no live disposable-database or platform-probe evidence in source closeout
- Oracle Linux package installation remains repository-managed at bootstrap time rather than a pre-baked immutable image
- final owner-build evidence is not available until the project owner runs the four locked commands against the final clean `main` SHA

These are explicit limitations, not source-gate passes.

## Conditions to begin V1

V1 business-engine work may begin only after:

1. the closeout commit is integrated into `main` and the repository is clean;
2. Windows-default and LF-only fresh clones pass the complete source-only suite with identical compatibility/capacity/readiness semantics;
3. the project owner supplies passing exact-SHA evidence for validation manifest, runtime contract validation, clippy, and workspace tests;
4. source integration readiness becomes `passed`, with no `blocked` or `failed` entry;
5. the project owner approves the V0 checkpoint/tag candidate `v0-platform-foundation-ready`.

A tag is intentionally not created by this closeout work.

## First OCI apply prerequisites

Before any first apply, all of the following require explicit owner review:

- passing V0 owner-build and source-readiness evidence bound to the same `main` SHA;
- reviewed non-secret tfvars, pinned Oracle Linux image OCID, availability domain, tenancy/compartment targets, and public SSH key;
- cost acknowledgement for the 4 OCPU/24 GiB profile;
- SSH remains disabled unless an exact non-world allowlist and time-bounded exception are approved;
- durable state/locking and single-writer ownership are approved;
- the saved plan is produced with `-refresh=false`, converted to JSON, validated by `validate_oci_plan.py`, and checksummed;
- plan JSON shows exactly the expected V0 resources, no destructive replacement outside the reviewed host/bootstrap intent, no public database/runtime/telemetry ports, and no secret material;
- object-storage/IAM policies remain bounded and do not grant backup deletion;
- apply command, operator, approval reference, saved-plan digest, rollback/recovery procedure, and maintenance window are recorded.

The exact non-mutating plan workflow is in `operations/integration/first-reviewed-oci-plan-v0.md`.

## Intentionally not done

This decision does not implement product authentication, identity, social/match/conversation/party/trust domains, Supabase migration, Kubernetes, Kafka, Redis, production deployment, DNS, certificates, real backup/restore, load testing, HA, paid-resource approval, or UI/admin console work.

## Consequences

V0 now has one direct provider-to-consumer source graph, deterministic cross-platform bytes, explicit capacity and connection admission, and fail-closed promotion semantics. The source checkpoint can be integrated into `main` before build evidence exists, but it must be reported as `V0 NOT READY` until the four owner records pass against the final SHA.
