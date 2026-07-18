# ADR 3000: Bounded sequence-diff native kernel V1

- Status: accepted for source integration; production enablement blocked on ARM64/BEAM evidence
- Date: 2026-07-18
- Owner: Senior 3
- Consumers: Senior 1, Senior 4, Senior 5

## Context

Realtime session resume needs compact missing ranges and duplicate accounting over a bounded ordered sequence window. The durable event stream, cursor and authorization remain owned by PostgreSQL and the Elixir runtime. Native code receives only a value snapshot and returns a deterministic value result.

The initial task proposed compact sequence diff or bounded dedup as the first useful kernel. No evidence supports moving session lifecycle, storage, network I/O or long-running work into Rust. A speculative isolated process would add protocol and operational cost without a V1 workload that needs it.

Official Erlang NIF guidance says a well-behaved regular NIF should return within roughly one millisecond and that longer work belongs on dirty schedulers or another isolation mechanism. Rustler 0.38 uses NIF ABI feature selection at compile time and loads a precompiled artifact through the owning OTP application's `priv/native` directory.

## Decision

Implement `compact_sequence_diff` version `1` as an optional regular Rustler NIF with a pure Elixir reference implementation.

The input contract is:

- `expected_first` and `expected_last`: unsigned 64-bit inclusive bounds.
- `observed_big_endian`: zero or more unsigned 64-bit values encoded big-endian and ordered non-decreasingly.
- maximum observed count: 2,048.
- maximum input bytes: 16,384.
- maximum expected window span: 65,536.
- maximum output ranges: 2,049.
- maximum native range storage estimate: 32,784 bytes.

The algorithm is a single O(n) pass. It borrows the BEAM input binary, allocates one bounded vector for output ranges, accepts adjacent duplicates, and rejects out-of-order or out-of-window input. It performs no disk, network, database or lock-based I/O and creates no unmanaged thread.

The Rust NIF is explicitly annotated `Normal`. The caller owns admission, deadline and telemetry. V1 declares maximum native concurrency `2` on the A1 host; the kernel creates no provider-owned queue. Senior 1 must reject before calling when its admission budget is exhausted.

`Liqi.Native.SequenceDiff` exposes three policies:

- `:reference`: never attempts native loading.
- `:native_preferred`: negotiates exact kernel/version/NIF ABI/scheduler class and falls back only for unavailable, incompatible or panic outcomes.
- `:native_required`: fails closed when the exact capability is unavailable.

Validation errors never fall back because native and reference implementations share the same input semantics. Execution metadata identifies native/reference/fallback behavior so Senior 1 can emit shared telemetry without a second instrumentation authority.

The native capability ships disabled. Production enablement requires direct BEAM-to-NIF p99 below 500 microseconds, hard maximum below 1 millisecond for the declared benchmark case, and a scheduler-starvation probe on the OCI A1 host.

No isolated Rust service is implemented in this ADR. `rust-port-protocol-v1.schema.json` reserves a bounded, versioned contract for a future workload that proves blocking, I/O-heavy or blast-radius isolation requirements. It is not a production capability declaration.

## Failure and recovery

Rust validation errors map to stable `NATIVE_*` errors. The NIF wrapper catches unwind-capable Rust panics and maps them to `NATIVE_PANIC`. Optional failure cannot stop durable commands because the pure Elixir implementation is always deployable and the durable event source remains outside Rust.

Missing or incompatible artifacts make optional readiness degraded but ready; required mode is not ready. Artifact rollback requires no database migration. Version `1` remains supported for at least one release window after a successor is introduced.

## Capacity and observability

The provider declares call count, latency, input bytes, errors, panics, fallbacks, admission rejections and native memory estimates. Payloads and event identifiers are forbidden as metric labels or logs. The direct benchmark records BEAM scheduler, dirty scheduler and async-thread configuration, even though this kernel does not use dirty schedulers.

## Alternatives rejected

- Pure Elixir only: remains the correctness baseline and rollback path, but does not provide the requested native walking skeleton.
- Dirty CPU NIF: unnecessary for a bounded single-pass kernel and would consume a more scarce scheduler pool.
- Rust port/service: adds framing, supervision and restart semantics without a blocking or long-running V1 workload.
- Hash-set dedup: increases allocation and does not exploit the ordered resume contract.
- Native ownership of cursor/session state: duplicates lifecycle and durable authority.

## Compatibility and removal

The API is additive and versioned. Senior 1 consumes the provider-owned `native/elixir` path dependency and does not copy its semantics. Senior 4 installs the exact verified shared object into the `liqi_native` OTP application's `priv/native` directory. Senior 5 accepts only benchmark evidence bound to exact source SHA and artifact checksum.

Remove the NIF while retaining the reference path if A1 evidence misses the p99/hard budget, causes scheduler starvation, or fails to outperform the end-to-end reference path enough to justify its maintenance cost.

## References

- Erlang/OTP NIF scheduling guidance: https://www.erlang.org/doc/apps/erts/erl_nif.html
- Rustler 0.38 documentation: https://rustler.hexdocs.pm/
- Rustler upgrade and NIF ABI guidance: https://rustler.hexdocs.pm/upgrade.html
