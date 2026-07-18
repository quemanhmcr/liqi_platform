# ADR 4004: Retained V0 rollback descriptors and migration-8 compatibility

- Status: Accepted; source integration complete, live evidence pending
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 1, Senior 2, Senior 5
- Integrated provider graph: `15e2dd5a263decb91308a0d1783c4610bd7dc62d`
- Senior 2 provider head in that graph: `168f6b3be66ff36eac4b4944f8d6940b6d2026ce`

## Context

V0 runs three Rust services while V1 runs one BEAM release. Both use `/opt/liqi/current`, but their lifecycle, health and configuration semantics differ. A symlink alone is not rollback proof: the retained release must be compatible with the live PostgreSQL migration and must have its own validated service/config/health descriptor.

UID 2210 is already the V0 `liqi-api` identity. V1 therefore retains V0 UIDs 2210–2212 and assigns `liqi-beam` UID 2220.

Senior 2 now publishes an executable migration-4-to-8 compatibility test. It proves migration 8 and Oban migration 14 are ready while the V0 command and realtime functions remain present. The durable migration remains forward-only; application rollback does not run a down migration.

## Decision

Every retained release has a host-local `release-target-v1` descriptor containing:

- runtime generation, exact Git SHA and immutable release path;
- retained source-manifest checksum;
- ordered systemd services and bounded drain/health commands;
- database migration interval and rollback-safe boundary;
- checksummed database compatibility evidence;
- predeclared rollback target;
- release-specific runtime config and required credential identities when applicable.

The V0 adapter accepts migration 8 only when supplied the exact passed Senior 2 result:

```json
{"test":"v0-upgrade-compatibility-v1","fromMigration":4,"toMigration":8,"v0FunctionsRetained":true,"passed":true}
```

It records the exact Senior 2 Git SHA and source-result checksum in `v0-rollback-compatibility-v1` evidence. Without that result, the descriptor remains at the original V0 range and activation fails closed.

The release controller verifies target and fallback evidence, live migration readiness, config paths and credential permissions before stopping services. It switches `/opt/liqi/current` and `/etc/liqi/runtime/current.json` only while the old runtime is stopped. Failed activation restores both selections and health-gates the original runtime. Public traffic is a separate approved operation.

## Database credential lifecycle

OCI Vault contains one bounded JSON login bundle for the seven PostgreSQL login roles. An approved provider command applies it through the local peer-authenticated PostgreSQL socket. It then derives:

- PgBouncer SCRAM userlist for pool-eligible roles only;
- direct local pgpass files for migration, monitoring and backup;
- the three-role URL bundle consumed by the BEAM runtime.

Transient files live under `/run/liqi/secrets`. Persistent evidence stores only the Vault bundle checksum, role names, paths and approval reference. On reboot the host regenerates transient files only when the current Vault bundle checksum equals the approved evidence; it does not silently rotate PostgreSQL credentials.

## Compatibility and rollback

Migration 5–8 remains additive during the V0 rollback window. V0 and V1 are route-scoped single writers; there is no durable dual write. Rollback never runs a database down migration.

Live activation still requires exact host evidence that PostgreSQL, PgBouncer, the approved credential bundle, migration 8 and both retained release descriptors are healthy. Until then the status remains `engineering-complete-evidence-pending`.
