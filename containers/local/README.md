# V1 local container preflight

This stack exercises the production-shaped BEAM runtime before an OCI VM is available. It is a disposable verification environment, not a production deployment target.

## What it proves

- PostgreSQL 17.10 starts with data checksums and applies the canonical eight migrations.
- The canonical eight least-privilege database roles exist.
- Runtime traffic reaches PostgreSQL through pgBouncer in transaction mode at `127.0.0.1:6432`.
- The BEAM release is built with Erlang/OTP 28.5.0.3, Elixir 1.20.2, Rust 1.97.1, Rustler 0.38.0, and a real x86_64 Linux NIF.
- The runtime starts with persistence, realtime dispatch, outbox delivery, Oban, and `native.mode=required`.
- Health, metadata, operator authorization, native parity, idempotent command handling, durable outbox delivery, and terminal observation all pass.
- The runtime and dedicated loopback ingress proxy use read-only root filesystems, drop all capabilities, disable restart, and enforce CPU, memory, and PID limits.

## Local-only database trust boundary

The runtime backend network is marked `internal`, PostgreSQL publishes no host port, and pgBouncer listens only in the shared pod namespace. A separate 32 MiB ingress proxy is the only dual-network service: it accepts `127.0.0.1:4100` on a small edge bridge and forwards only to the internal pod gateway. Runtime, pgBouncer, and PostgreSQL never join the edge network. Within the isolated backend, this stack uses PostgreSQL and pgBouncer `trust` authentication so no reusable database credential is stored on the workstation.

This exception is intentionally limited to `containers/local/**`. It does **not** validate the production SCRAM materialization flow; that remains covered by the infrastructure credential provider, source validators, and recovery gates. Never copy this authentication mode into OCI or a routable environment.

## Resource envelope

| Service | Memory | CPU | PID limit | Host exposure |
| --- | ---: | ---: | ---: | --- |
| PostgreSQL | 768 MiB | 1.0 | 160 | none |
| Database init | 256 MiB | 0.5 | 64 | none |
| Gateway / pod namespace | 32 MiB | 0.15 | 32 | none |
| Loopback ingress proxy | 32 MiB | 0.15 | 32 | `127.0.0.1:4100` only |
| pgBouncer | 64 MiB | 0.2 | 48 | none |
| BEAM runtime | 768 MiB | 1.25 | 128 | through gateway only |

Compose builds are serialized with `COMPOSE_PARALLEL_LIMIT=1`. Rust compilation is limited to two jobs.

## Run

Run inside the Ubuntu 24.04 WSL distribution with Docker available:

```bash
cd /path/to/liqi_platform
export LIQI_LOCAL_STATE_DIR=/opt/liqi-local-state/$(git rev-parse --short=12 HEAD)
containers/local/bin/smoke.sh
```

The smoke command builds sequentially, starts each dependency layer, verifies the full walking skeleton, writes `local-container-result.json`, and then removes containers, networks, volumes, and plaintext operator token files. Set `LIQI_LOCAL_KEEP_RUNNING=1` only for active debugging.

To keep the stack running:

```bash
containers/local/bin/up.sh
containers/local/bin/verify.sh
```

To stop and remove all runtime state:

```bash
LIQI_LOCAL_REMOVE_STATE_SECRETS=1 containers/local/bin/down.sh
```

## Evidence

The verifier writes a redacted document containing the exact Git SHA, image identity, runtime metadata, migration version, pgBouncer mode, native implementation, durable probe IDs, hardening checks, and a final status. Operator tokens and database values are never included.

## Disk cleanup

After evidence has been retained, remove build cache and unused images explicitly:

```bash
docker builder prune --all --force
docker image prune --all --force
docker volume prune --force
```

Docker is installed with bounded local logs (`10 MiB × 3 files` per container) and is not enabled for automatic startup in WSL.
