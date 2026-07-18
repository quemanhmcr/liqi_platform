# ADR 3001: Native artifact, panic profile and provenance V1

- Status: accepted for source integration; signed ARM64 artifact pending
- Date: 2026-07-18
- Owner: Senior 3
- Consumers: Senior 1, Senior 4, Senior 5

## Context

The V0 Rust workspace release profile uses `panic = "abort"`. Reusing that profile for a NIF would make Rust panics terminate the BEAM before `catch_unwind` or Rustler panic mapping could recover. The NIF also must be packaged for GNU ARM64 with identity, checksum, SBOM and provenance that Senior 4 can install without rebuilding or patching provider code.

The root Cargo profile is a shared file owned by the existing Rust workspace, so the native provider must preserve V0 behavior while adding only the profile required by the NIF artifact.

## Decision

Add a custom Cargo profile named `nif-release` that inherits release optimization settings but sets `panic = "unwind"`. V0 `[profile.release]` remains `panic = "abort"`. Only `native/scripts/build-arm64-artifact.sh` is an approved provider build command for the production-shaped NIF.

The build contract requires:

- Linux `aarch64` host and target `aarch64-unknown-linux-gnu`.
- Rust and Cargo `1.97.1`.
- Rustler crate `0.38.0` with NIF ABI `2.15`.
- clean tracked worktree at the exact source SHA.
- `CARGO_INCREMENTAL=0` and source-path remapping.
- at most two Cargo jobs.
- at least 2 GiB available memory and 4 GiB available disk before build.
- ELF64 little-endian AArch64 verification before packaging.

The build creates an unsigned local artifact only. Signing is a separate CI/operator step using a Sigstore keyless bundle:

```bash
cosign sign-blob --yes \
  --bundle libliqi_sequence_diff_nif.so.sigstore.json \
  libliqi_sequence_diff_nif.so
```

`package_artifact.py` verifies that bundle against an exact OIDC identity and issuer before generating an SPDX 2.3 SBOM, SLSA provenance v1 statement and `native-artifact-v1` manifest. `verify_artifact.py` rechecks schema, path containment, checksums, ELF ABI, SBOM subject, provenance source/artifact binding and the Sigstore bundle.

The manifest stores only relative paths. Private signing keys, OCI credentials and developer-machine absolute paths are forbidden. Generated artifacts remain under `.artifacts/` and are not committed.

## Consequences

A NIF built through the ordinary V0 release profile is rejected by contract. A checksum without provenance and verified signature is not an installable artifact. A fixture manifest cannot promote capability status beyond `source-ready`.

Senior 4 can copy the verified `libliqi_sequence_diff_nif.so` into `lib/liqi_native-1.0.0/priv/native/` during release assembly. Artifact rollback is a file/release rollback and has no database migration.

The custom unwind profile slightly increases artifact complexity and can reduce optimization opportunities compared with abort, but it is necessary for the promised panic mapping. The build is provenance-bound rather than claiming bit-for-bit reproducibility until repeated ARM64 builds prove it.

## Compatibility and migration

This is additive to the V0 workspace. Senior 1 adds the `native/elixir` path dependency; Senior 4 consumes the manifest and artifact; Senior 5 validates exact source/artifact evidence. Existing Rust V0 binaries continue using the unchanged release profile.

The old native artifact must be retained for one release window. A future NIF ABI, target triple or kernel API change requires a new artifact contract version or a compatibility adapter.

## References

- Cargo custom profiles and panic strategy: https://doc.rust-lang.org/cargo/reference/profiles.html
- Rust panic/unwind behavior: https://doc.rust-lang.org/stable/reference/panic.html
- Sigstore blob signing: https://docs.sigstore.dev/cosign/signing/signing_with_blobs/
- Sigstore blob verification: https://docs.sigstore.dev/cosign/verifying/verify/
