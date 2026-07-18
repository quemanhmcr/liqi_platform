# ADR 4004: V1 self-hosted control plane and independent recovery authority

- Status: Accepted
- Date: 2026-07-18
- Owner: V1 closeout
- Supersedes: ADR 4001 state-backend decision; ADR 4003 Object Storage artifact transport

## Context

The previous V1 design required OCI Object Storage S3 compatibility, AWS-style Customer Secret Keys, and a bucket-resident lock file for OpenTofu state. It also put application backup and signed artifact delivery behind the same cloud failure domain being reconstructed. This violates the recovery requirement that state, backups, WAL and rollback artifacts survive loss of the OCI application host and its workload resources.

## Decision

OpenTofu V1 live state uses the built-in PostgreSQL `pg` backend on independent management hardware. The database, schema and role are dedicated to infrastructure state; TLS uses `sslmode=verify-full`; locking uses PostgreSQL advisory locks; credentials are supplied only through protected libpq/OpenTofu environment variables. OpenTofu state and plan encryption remain enforced.

A live plan requires exact-SHA machine evidence for TLS, lock contention, encrypted state backup and isolated state restore. The application PostgreSQL backup/WAL repository and signed artifact archive also move to the independent management/storage plane. OCI Object Storage, its S3 compatibility API and Customer Secret Keys are not V1 dependencies.

The OCI application host may retain a local working data volume, but that volume is never backup authority. Public ingress remains TCP/80 and TCP/443 only. Management connectivity must be encrypted and independently evidenced before runtime activation.

## Consequences

The management host address, credentials, protected encryption material and physical backup destination are external inputs. Their absence blocks live plan/apply but does not justify falling back to local state, application-host state, OCI Object Storage or unencrypted transport.

Existing V0 Object Storage contracts remain historical rollback interfaces until separately versioned; they are not valid V1 live authority.
