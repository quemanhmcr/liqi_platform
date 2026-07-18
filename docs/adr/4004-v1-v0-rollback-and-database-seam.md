# ADR 4004: Retained V0 rollback descriptors and the blocked V1 database credential seam

- Status: Accepted; database integration evidence pending
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 1, Senior 2, Senior 5

## Context

V0 runs three Rust systemd services while V1 runs one BEAM release. Both use `/opt/liqi/current`, but their start, drain and health semantics differ. Copying V0 activation logic into a BEAM-specific script would create competing lifecycle semantics. A V1 release also cannot be activated safely until its declared migration interval overlaps both the live database version and the retained V0 rollback-safe range.

During implementation, a concrete compatibility bug was found: UID 2210 is already the V0 `liqi-api` identity. Reusing it for `liqi-beam` would have changed file ownership and broken rollback. V1 therefore retains V0 UIDs 2210–2212 and assigns `liqi-beam` UID 2220.

Senior 2's integrated V0 provider supplies schema, migrations, readiness, backup, WAL and isolated restore evidence. It creates database roles, but no V1 contract currently supplies the live role-password materialization/bootstrap step that makes PostgreSQL/PgBouncer directly consumable by the BEAM release.

## Decision

Every retained release has a host-local `release-target-v1` descriptor. The descriptor identifies:

- Runtime generation (`rust-v0` or `beam-v1`).
- Exact Git SHA and immutable release path.
- Source manifest checksum and retained evidence path.
- Ordered systemd units.
- Bounded drain and health commands.
- Database migration interval and rollback-safe boundary.
- Exact predeclared rollback target.
- Required config and credential paths.

The provider controller performs preflight, drain, stop, atomic symlink selection, ordered start and health gate. Activation failure attempts automatic restoration of the original descriptor. It never executes a database down migration and never enables traffic. V0 manifests/deployment specs/health targets are validated against the existing V0 contracts before becoming a rollback descriptor.

The existing Senior 2 backup/recovery implementation is packaged unchanged under `/usr/local/lib/liqi-database`; infrastructure only adapts paths, credentials and systemd resource limits. Backup timers remain disabled until checksummed recovery-ready backup status and a successful isolated restore result for that backup are supplied.

## Blocked seam

Provider: Senior 2

Consumer: Senior 4 activation and host-readiness providers; Senior 1 Mix release

Missing contract: a V1 database host-bootstrap/credential result that identifies required Vault credential names, PostgreSQL role password application semantics, PgBouncer userlist materialization, exact migration/readiness commands, and rollback-compatible migration evidence.

Why current work cannot safely continue: inventing password/application semantics in infrastructure would duplicate database authority and could produce a PgBouncer/PostgreSQL mismatch.

Minimal provider output required: a signed or checksummed manifest plus idempotent command(s) that consume service-scoped credentials and produce `database-readiness-v0` or a versioned additive successor.

Temporary work that remains independent: OCI plan, hardened host, signed host bundle, fail-closed edge, release/native staging, V0 retention, activation dry-run, Vault references, backup/recovery packaging and all source gates.

Owner of removal: Senior 2 publishes the seam; Senior 4 removes the activation blocker and records direct-provider integration evidence.
