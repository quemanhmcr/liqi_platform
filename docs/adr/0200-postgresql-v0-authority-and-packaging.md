# ADR 0200: PostgreSQL V0 authority and packaging

## Status

Accepted.

## Context

LIQI V0 runs on one OCI `VM.Standard.A1.Flex` host with 4 OCPU, 24 GB RAM and a 200 GB combined boot/block-storage envelope. The data plane must be reproducible and recoverable without claiming high availability that the topology does not provide.

PostgreSQL publishes a new major roughly yearly and supports each major for five years. PostgreSQL 17 is supported until November 2029. PostgreSQL 18 is newer, but V0 does not need a PostgreSQL 18-only capability.

## Decision

- PostgreSQL 17 is the V0 compatibility major. The minimum security baseline is 17.10; operators track the current PostgreSQL 17 security minor.
- PostgreSQL, PgBouncer and pgBackRest use reviewed binary system packages and systemd services. V0 does not compile database infrastructure from source and does not put PostgreSQL in an application container.
- PostgreSQL packages come from the PostgreSQL Global Development Group repository where supported by the Senior 1 host operating system. PgBouncer and pgBackRest come from PGDG or another explicitly reviewed OS repository.
- PostgreSQL owns the durable state. PgBouncer, realtime processes, workers and projections are replaceable consumers.
- The single-node cluster has one primary and no Patroni, etcd or simulated failover layer.
- PostgreSQL and PgBouncer listen only on Unix sockets or loopback in V0. Future split-node deployments use private networking and `tls-verify-full` without changing the runtime configuration shape.
- Runtime traffic uses PgBouncer transaction pooling. Migration, backup administration and database probes use the restricted direct endpoint.

## Compatibility rationale

PostgreSQL 17 provides the required transaction, locking, JSONB, role, WAL archiving and observability primitives. Selecting a mature supported major reduces early operational variance while retaining a multi-year support window. Major upgrades require a separate compatibility decision and a restore drill. Minor upgrades are treated as security maintenance, not optional feature work.

## Resource envelope

- PostgreSQL plus PgBouncer steady-state memory target: no more than 6.5 GiB.
- Database subsystem hard memory ceiling: 10 GiB.
- PostgreSQL `max_connections`: 80; PgBouncer converts at most 300 bounded client sessions into 35 runtime and 5 operational server connections.
- V0 durable database data cap: 8 GiB while relying on the 20 GiB Always Free Object Storage allowance for encrypted backup retention.
- Database data, WAL/spool, logs and restore scratch use an explicit 130 GiB disk budget. Crossing the V0 cap requires a PAYG/capacity decision rather than silent retention loss.

## Consequences

Transaction pooling prohibits session state, temporary tables, session advisory locks and `LISTEN/NOTIFY` through the runtime pool. Named prepared statements are disabled unless the selected driver proves compatibility with the deployed PgBouncer version. Senior 3 must encode these restrictions in the persistence adapter.

The host package mapping and directory materialization remain Senior 1 responsibilities. This ADR specifies the database capability and runtime contract, not OCI mutation or host bootstrap implementation.

## Sources

- PostgreSQL versioning policy and supported-version lifecycle.
- PostgreSQL binary-package guidance for Red Hat-family systems.
- PgBouncer pooling semantics and connection limits.
