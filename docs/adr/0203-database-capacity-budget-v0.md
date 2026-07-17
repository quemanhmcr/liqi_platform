# ADR 0203: Database capacity budget inside the V0 single-node envelope

## Status

Accepted.

## Context

The hard V0 host is 4 OCPU, 24 GiB RAM and 200 GiB combined storage. At least 1 OCPU, 4 GiB RAM and 20 GiB disk remain reserved for the operating system, incident spikes and recovery. PostgreSQL must coexist with the Rust API, realtime gateway, worker, edge and telemetry processes.

Senior 3 now owns the runtime demand declaration of 35 PostgreSQL connections: API 20, realtime 5 and worker 10. Database capacity must not count those connections again.

## Decision

The machine-readable provider budget is `contracts/platform/database-capacity-budget-v0.json`:

| Component | Hard OCPU | Hard memory | Hard disk | Provider-owned PostgreSQL connections |
|---|---:|---:|---:|---:|
| PostgreSQL authority | 0.8 | 6144 MiB | 118 GiB | 8 non-recovery direct/administrative |
| PgBouncer boundary | 0.1 | 256 MiB | 0.2 GiB | 40 pooled server capacity |
| Database recovery | 0.3 | 1536 MiB | 12 GiB | 2 direct recovery |
| **Total** | **1.2** | **7936 MiB** | **130.2 GiB** | **50 server reservation** |

PostgreSQL `max_connections=80` is partitioned as:

```text
35 runtime pooled, declared once by Senior 3
5 operational pooled: liqi_readonly=3 and liqi_monitor=2
10 direct administrative/recovery
30 reserved headroom
```

PgBouncer `max_db_connections=40` is therefore the 35 runtime plus 5 operational pool. The database provider capacity artifact declares 40 PgBouncer server slots and 10 direct slots. Senior 4 accounts Senior 3's 35 as pooled demand, not as an additional server reservation; it verifies `35 <= 40` and reports the 50 database server slots separately.

Database recovery is default-enabled but permission-gated. Backup and repository check share one host lock, so their limits do not overlap. Backup is throttled to 30% of one CPU and 1536 MiB maximum. This accepts a longer backup duration to preserve the hard host reserve; the 8 GiB V0 database cap and 60-minute restore target remain the controlling validation envelope.

The 118 GiB PostgreSQL disk hard limit includes data, WAL and database logs. Recovery receives 12 GiB local staging/spool/scratch. Durable backup remains off-host in encrypted OCI Object Storage under the separate 18 GiB Always Free safety cap.

## Failure behavior

- PostgreSQL memory pressure rejects/cancels work or enters manual recovery; swap is not capacity.
- PgBouncer saturation rejects new clients rather than opening unbounded server connections.
- The promotion observer uses at most one readonly slot and is not a persistent runtime pool.
- A concurrent backup/repository operation is rejected by the shared lock.
- Recovery staging or Object Storage cap exhaustion blocks new backup work.
- No component silently raises a hard limit.

## Compatibility

Transaction pooling remains the V0 default. Changing pooling mode, role pool caps or the 35/5/10/30 partition is a database contract change for Senior 3. Increasing a hard resource limit requires an aggregate result proving the 4-OCPU/24-GiB host reserve remains intact or a PAYG decision note.
