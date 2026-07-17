# ADR 0407: Host-local health-gated activation

## Status

Accepted for V0.

## Decision

Activation is implemented as a host-local, dry-run-by-default orchestration command. It consumes the immutable deployment specification, Senior 1 host-readiness evidence, Senior 2 database-readiness evidence, staged artifact digests and the Senior 3 health target/systemd units.

Execution requires root, the exact reviewed deployment-spec digest and an explicit owner approval reference. Activation does not perform OCI mutation or database migration.

The deployment specification additively exposes `preflight.required_database_migration`. Activation requires the Senior 2 readiness result to report `database-v0`, `ready=true`, `reason=ready`, a matching `requiredVersion`, and `currentVersion >= requiredVersion`.

A failed new release is stopped. A retained predeclared application release is selected and health-gated. Database rollback is never attempted. Missing or failed application rollback enters incident state.

## Consequences

- Single-node replacement has a controlled downtime window and is not advertised as canary, HA or zero downtime.
- Provider evidence remains authoritative; Senior 4 does not query or repair provider internals.
- Windows and CI can execute dry-run validation. Mutation is POSIX-root-only.
- Exit `3` records successful application rollback; exit `4` records an incident.
