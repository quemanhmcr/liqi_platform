# ADR 0204: V1 independent pgBackRest TLS repository and reconstructable backup evidence

## Status

Accepted for V1. Supersedes ADR 0202 for V1 live deployment; ADR 0202 remains historical V0 compatibility context.

## Context

The V1 application host, its local PostgreSQL primary and its block volume are one failure domain. OCI Object Storage, S3 compatibility credentials and a bucket in the same cloud control plane are no longer required architecture. Loss of the OCI host must not remove the backup/WAL authority, the evidence needed to select a backup, or the material needed to rebuild the host.

A second bespoke metadata API would add another lifecycle and recovery owner. pgBackRest already stores immutable backup identity, timestamps, WAL bounds and user annotations inside the repository. Those annotations can carry the exact LIQI source, migration and recovery-probe identity needed to reconstruct JSON evidence after application-host loss.

## Decision

- PostgreSQL remains durable application authority; pgBackRest remains physical backup and WAL archive tool.
- Repository V1 is encrypted POSIX storage on independent management hardware at `/independent-storage/pgbackrest/liqi`.
- The application host connects to pgBackRest server over mutual TLS on port 8432 through the outbound-established WireGuard management network. The service is not publicly exposed.
- pgBackRest client-side `aes-256-cbc` encryption remains mandatory. The repository client key and cipher passphrase are transient credential files, never persistent config or command arguments.
- No OCI Object Storage bucket, S3 API, AWS-style Customer Secret Key or application-host filesystem is V1 backup authority.
- Each backup annotation records exact source Git SHA, source host reference, PostgreSQL version, migration version, migration-manifest digest, recovery-probe identity/state, repository identity/path/port and a unique run ID.
- `database-backup-metadata-v1` is derived evidence. It is created or reconstructed from `pgbackrest info --output=json`; the repository annotations are durable metadata authority.
- Repository capacity is published by the management provider as checksummed, exact-SHA, short-lived evidence. The application backup command rejects missing, stale, tampered, SHA-mismatched or insufficient capacity evidence before creating a recovery probe or repository write.
- Repository filesystem backup, certificate lifecycle, retention monitoring and repository restore proof belong to the independent management/storage provider. Database restore and invariant verification remain owned under `database/**`.
- Full retention is 2 and differential retention is 6. Changing retention or capacity requires evidence that at least one separately restored recovery point remains valid.

## Recovery modes

### First provision

Prove no previous live authority, establish the independent repository and current capacity evidence, initialize PostgreSQL/PgBouncer/WAL archiving, migrate to version 8, start traffic-disabled runtime, take an immediate full backup, reconstruct/validate metadata, perform an isolated restore and only then enable command traffic.

### Upgrade

Require a fresh pre-migration backup, a recent isolated restore proof, additive migration compatibility and a route-scoped application rollback target. Database down-migrations are not the rollback mechanism.

### Restore rebuild

Fence traffic, select a retained pgBackRest label/PITR target, restore below the isolated recovery root, verify migration/probe/outbox invariants, preserve checksummed evidence, and activate only through controlled release/cutover tooling.

## Consequences

Management host address, WireGuard peer configuration, CA/certificates, cipher passphrase, independent storage and its backup destination are external inputs. Their absence blocks live backup, restore proof and cutover. It does not permit local-only backup, unencrypted transport or a fallback to S3/Object Storage.
