# ADR 0202: Encrypted pgBackRest repository and isolated restore verification

## Status

Superseded by ADR 0204 for V1. Retained as historical V0 compatibility context.

## Context

V0 has one PostgreSQL node and one real failure domain. A backup on the boot/block volume does not survive loss of that host or volume. Recovery must therefore use an external repository, continuous WAL archiving, immutable metadata and a restore drill that starts an isolated PostgreSQL cluster and checks durable invariants.

OCI Object Storage exposes an Amazon S3 Compatibility API. It requires an Object Storage namespace, region endpoint and Customer Secret Key pair. These credentials are secrets and cannot be stored in Git or rendered into the persistent pgBackRest configuration.

## Decision

- pgBackRest is the physical backup and WAL archive tool.
- Repository format V0 is `pgbackrest-encrypted-s3-v0` on a dedicated OCI Object Storage bucket through the S3 path-style endpoint `<namespace>.compat.objectstorage.<region>.oci.customer-oci.com`.
- Repository files are encrypted client-side with pgBackRest `aes-256-cbc`. The S3 access key, S3 secret and repository cipher passphrase are materialized as systemd credentials or root-only tmpfs files and supplied through process environment variables. They are never written to generated configuration, command arguments, logs or metadata.
- The repository path is `/postgresql/v0`. Full retention is 2 and differential retention is 7. Bundling is enabled to reduce Object Storage request and small-object overhead.
- PostgreSQL invokes a minimal, fixed-path `pgbackrest-command.sh` boundary because systemd credentials are files rather than persistent plaintext configuration. pgBackRest warns that wrappers can create unpredictable behavior, so the wrapper performs no command rewriting: it validates three root/postgres-readable single-line credentials, exports only the documented `PGBACKREST_*` options, and `exec`s the reviewed pgBackRest binary unchanged. The `cmd` option pins generated restore commands to the same boundary. Enabling archiving requires the secret-boundary test, `stanza-create/check`, a full backup and an isolated restore drill.
- Schedule is one full backup each Sunday at 02:00 UTC and differential backups Monday through Saturday at 02:00 UTC. `archive_timeout=5min` establishes the working RPO target when archive health is green.
- Backup completion requires pgBackRest archive consistency checking, a completed platform recovery probe, machine-readable metadata, a SHA-256 sidecar and append-only publication of metadata followed by its SHA-256 completion marker through OCI instance-principal authentication.
- Backup metadata is non-secret. It identifies the pgBackRest label, PostgreSQL major, migration version/manifest, recovery probe and repository compatibility. The repository encryption passphrase is required for the full retention window.
- Restore is never in-place by default. The command rejects the production `PGDATA`, restores to a new directory below a configured restore root, disables archive mode, starts PostgreSQL on an isolated Unix socket and high port, and emits `database-restore-result-v0`.
- Verification checks metadata checksum/schema, PostgreSQL major, migration version and checksums, absence of unresolved migration failures, probe state, terminal outbox state and exactly one terminal effect. Process exit code alone is insufficient.
- The observed V0 RPO is the elapsed time between the committed recovery probe and backup stop. This is a conservative, evidence-backed marker of data definitely present after restore; it is not a claim of zero data loss. The observed RTO is the measured isolated restore-and-verify duration rounded up to whole seconds.
- `database/bin/recovery-status.sh` maps current archive health, backup metadata and checksummed restore evidence directly to Senior 4's `recovery-status-v0`. Senior 4 does not parse pgBackRest or synthesize recovery semantics.

## Bounded failure behavior

The asynchronous archive push queue is bounded at 2 GiB. pgBackRest documents that exceeding this bound drops queued WAL to keep PostgreSQL available, which breaks the continuous archive chain. When this happens:

1. PostgreSQL remains data authority and application readiness is not made permissive or false by inference.
2. Recovery readiness becomes failed immediately.
3. Operators stop claiming the 5-minute RPO.
4. A new full backup is required after Object Storage connectivity and capacity are restored.

The spool directory must be placed on a Senior 1 host path with an explicit disk budget and monitoring. Object Storage usage is capped operationally at 18 GiB for V0; crossing the cap requires a PAYG/capacity decision rather than deleting the only valid backup silently.

## Compatibility and retention

A pgBackRest package upgrade must retain the ability to read every backup in the current retention window. A repository format/tool change requires parallel restore support until all old backups expire and at least one restore drill succeeds with the new format. PostgreSQL major upgrades require a separate ADR and recovery drill.

## Non-goals

- No standby, Patroni, etcd or failover automation.
- No logical-only backup presented as PITR.
- No backup bucket, Customer Secret Key or IAM resource creation in this branch.
- No automatic OCI mutation during source validation.

## Sources

- PostgreSQL 17 continuous archiving and point-in-time recovery documentation.
- pgBackRest configuration and restore references.
- OCI Object Storage Amazon S3 Compatibility API documentation.
