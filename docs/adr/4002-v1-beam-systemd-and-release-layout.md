# ADR 4002: V1 BEAM systemd isolation and immutable release layout

- Status: Accepted for source implementation
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 1, Senior 3, Senior 5

## Context

The V0 Rust units use `MemoryDenyWriteExecute=yes`. Erlang/OTP enables a JIT on supported platforms and V1 also loads Rustler shared libraries. Denying writable-to-executable mappings can prevent the VM from operating correctly. Disabling the JIT only to preserve a generic hardening flag would change the capacity/performance model without evidence.

## Decision

`liqi-beam.service` does not enable `MemoryDenyWriteExecute`. It retains non-root execution, empty capability sets, `NoNewPrivileges`, a strict read-only filesystem, protected home/kernel/control-group namespaces, bounded address families, task/memory/CPU limits, restart bounds and explicit writable paths. Native concurrency and memory limits remain runtime/provider controls and are validated from the Senior 3 manifest.

Releases are installed under `/opt/liqi/releases/<release-id>`, made immutable after validation, and activated through an atomic `/opt/liqi/current` symlink. The production host never compiles source. A provider-owned launcher reads the validated installed manifest and executes Senior 1's argv arrays without shell evaluation. Drain and health are separate bounded commands.

A release must contain ERTS and target `aarch64-unknown-linux-gnu`; it carries exact Git SHA, checksum and Ed25519 signature identity. Native artifacts are independently checksummed and signed. V0 remains an exact rollback target until the migration window is explicitly closed.

## Consequences

The BEAM service has one deliberate hardening exception instead of a non-functional unit. The exception is narrow and observable. Activation remains blocked when provider commands, signatures, ABI evidence, database compatibility or rollback identity are missing.
