# ADR 4000: V1 OCI live provider boundary and evidence states

- Status: Accepted for source implementation
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 1, Senior 2, Senior 3, Senior 5

## Context

V1 replaces the Rust application process topology with a BEAM/Phoenix release while retaining PostgreSQL as durable authority and the integrated V0 Rust release as a rollback target. The V1 provider branches were created from integrated `main` SHA `4c561515f46237acfaf64e0145e37e54a6c4c9d9`; no approved `v0-platform-foundation-ready` tag exists. At the start of this work, the Senior 1, Senior 2 and Senior 3 V1 branches contain no V1 artifact contracts or build outputs.

The task assignment authorizes source work, local validation and artifact preparation. It does not authorize `tofu apply`, OCI resource mutation, live deployment, live database migration, secret rotation, backup/restore mutation or traffic activation.

## Decision

Senior 4 publishes two kinds of seams:

1. `contracts/infrastructure/**` describes the OCI environment and host-readiness evidence.
2. `contracts/deployment/**` describes artifact installation identity, activation, rollback and endpoint evidence.

The deployment native-artifact contract is an installation handoff only. Scheduling class, ABI and safety evidence remain authored by Senior 3; Senior 4 validates and installs them without redefining native runtime semantics. The Mix release handoff similarly consumes Senior 1's release name, commands and health/drain behavior rather than inventing them.

Source fixtures always use `engineering-complete-evidence-pending`. A fixture cannot become production evidence. Live evidence must bind the exact Git SHA, release ID, artifact checksums, approval reference and observed host state.

The provider state machine is:

`planned → staged → preflight-passed → activated → health-gated → traffic-enabled → verified`

Failure proceeds through `activation-failed → draining → rolled-back → health-gated`. Traffic enablement is a distinct approved action; a running systemd process is not deployment success.

## Compatibility and migration

V0 artifacts, configuration and activation path remain retained for the migration window. A V1 manifest declares the database migration interval and `rollback_safe_through` version. Activation is blocked unless a known rollback target exists and both configurations are compatible. No permissive fallback is allowed when Senior 1, Senior 2 or Senior 3 provider output is absent.

Temporary state: source examples represent missing live evidence. Owner: Senior 4. Removal condition: replace examples with separately stored exact-SHA machine evidence after approved OCI apply and live verification; examples remain only as schema fixtures.
