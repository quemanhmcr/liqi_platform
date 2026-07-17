# ADR 0203: Database capacity budget inside the V0 single-node envelope

## Status

Accepted.

## Context

The V0 host has 4 OCPU, 24 GiB RAM and 200 GiB combined storage. At least 1 OCPU, 4 GiB RAM and 20 GiB disk remain reserved for the operating system, incident spikes and recovery. PostgreSQL must coexist with the Rust API, realtime gateway, worker, edge and telemetry processes; it cannot reserve the complete provider envelope.

## Decision

The machine-readable provider budget is `contracts/platform/database-capacity-budget-v0.json`:

| Component | Hard OCPU | Hard memory | Hard disk | PostgreSQL connections |
|---|---:|---:|---:|---:|
| PostgreSQL authority | 1.0 | 6144 MiB | 118 GiB | 40 direct/reserved |
| PgBouncer boundary | 0.1 | 256 MiB | 0.2 GiB | 40 pooled server connections |
| Database recovery | 0.4 | 1536 MiB | 12 GiB | 2 |
| **Total** | **1.5** | **7936 MiB** | **130.2 GiB** | **82** |

The connection accounting is intentionally conservative. PostgreSQL `max_connections=80` is partitioned into 40 pooled runtime server connections and 40 direct/reserved connections. The recovery budget declares two transient direct sessions; Senior 4's aggregate capacity check may count them in addition to the PostgreSQL partition to preserve safety margin. Runtime client concurrency is not mapped one-for-one to PostgreSQL connections because PgBouncer transaction pooling is the boundary.

Database recovery is default-enabled as a platform capability but all repository mutation remains permission-gated. Full/differential backup and repository check share one host lock, so their hard limits do not overlap. Backup runs with 40% CPU quota and 1536 MiB memory maximum; repository health runs with tighter limits.

The 118 GiB PostgreSQL disk hard limit includes data, WAL and database logs under the Senior 1 data-volume contract. Recovery receives 12 GiB local staging/spool/scratch. Durable backup data remains off-host in the dedicated encrypted Object Storage repository and is constrained separately by the 18 GiB Always Free safety cap.

## Failure behavior

- PostgreSQL memory pressure must reject/cancel work or enter manual recovery; swap is not capacity.
- PgBouncer saturation rejects new clients rather than opening unbounded server connections.
- A concurrent backup/repository operation is rejected by the shared lock.
- Recovery staging or Object Storage cap exhaustion blocks new backup work and requires operator/capacity action.
- No component silently raises a hard limit. A change must update this artifact, the database provider contract, systemd limits and Senior 4 aggregate validation together.

## Compatibility

Changing pooling mode or server-connection allocation is a consumer contract change for Senior 3. Increasing any hard resource limit requires an aggregate capacity result proving the V0 reserve remains intact or a PAYG decision note.
