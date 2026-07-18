# LIQI V1 native provider

This directory is the Senior 3 provider seam for the optional bounded sequence-diff Rustler kernel. Rust owns acceleration only. PostgreSQL and the Elixir runtime retain durable state, identity, deadlines, admission and lifecycle.

## Source layout

- `sequence-diff-core`: safe deterministic kernel and differential/property corpus.
- `sequence-diff-nif`: Rustler adapter, regular scheduler annotation, panic/error mapping and capability metadata.
- `elixir`: provider-owned `:liqi_native` Mix path dependency, pure reference, fallback/readiness API and A1 benchmark module.
- `fuzz`: Linux/nightly `cargo-fuzz` parity target; not a production path.
- `scripts`: ARM64 build, SBOM/SLSA packaging and artifact verification.
- `tests`: machine-readable source and contract gates.

No isolated Rust process is enabled in V1. The port protocol schema is reserved and does not declare a service ready.

## Senior 1 integration

Add the provider directly as a path dependency in the Senior 1 root Mix project:

```elixir
{:liqi_native, path: "native/elixir"}
```

Call only `Liqi.Native.SequenceDiff.compact/4` and `readiness/1`. Keep the feature default `:reference` or `:native_preferred` with native disabled until exact A1 evidence is accepted. Senior 1 owns admission, deadline and telemetry emission. The returned execution metadata contains implementation, fallback and reason fields; do not log input payloads.

The walking probe should use a bounded ordered binary, compare the native-preferred result with `Liqi.Native.Reference.SequenceDiff.compact/3`, and prove reference mode after native disablement. Native output is never the sole durable correctness path.

## Local source validation

```bash
bash native/tests/run-source-validation.sh --rust-only
```

The full gate also runs Elixir reference/fallback tests and intentionally exits blocked when Elixir is unavailable:

```bash
bash native/tests/run-source-validation.sh
```

Run fuzzing on Linux with nightly Rust and `cargo-fuzz` installed:

```bash
LIQI_FUZZ_SECONDS=300 bash native/fuzz/run-fuzz.sh
```

The source-only Rust benchmark is diagnostic, not production evidence:

```bash
cargo +1.97.1 run --release \
  -p liqi-sequence-diff-core \
  --example core_benchmark -- 20000
```

## Senior 4 ARM64 artifact workflow

No command below mutates OCI. Run the build only on a controlled Linux ARM64 builder with a clean checkout:

```bash
export LIQI_RELEASE_ID='<release-id>'
export LIQI_SOURCE_REVISION='<40-character-git-sha>'
export LIQI_NATIVE_BUILD_JOBS=2
export LIQI_NATIVE_OUTPUT_DIR="$PWD/.artifacts/native/$LIQI_RELEASE_ID"
bash native/scripts/build-arm64-artifact.sh
```

Sign the generated blob in an approved OIDC-enabled CI/operator context:

```bash
cd "$LIQI_NATIVE_OUTPUT_DIR"
cosign sign-blob --yes \
  --bundle libliqi_sequence_diff_nif.so.sigstore.json \
  libliqi_sequence_diff_nif.so
```

Package and verify evidence:

```bash
python native/scripts/package_artifact.py \
  --artifact-dir "$LIQI_NATIVE_OUTPUT_DIR" \
  --release-id "$LIQI_RELEASE_ID" \
  --source-revision "$LIQI_SOURCE_REVISION" \
  --builder-id 'https://github.com/liqi-platform/liqi_platform/.github/workflows/native-artifact.yml' \
  --signature-identity '<exact-certificate-identity>' \
  --signature-issuer 'https://token.actions.githubusercontent.com'

python native/scripts/verify_artifact.py \
  --manifest "$LIQI_NATIVE_OUTPUT_DIR/native-artifact-$LIQI_RELEASE_ID.json"
```

Install the verified shared object at:

```text
lib/liqi_native-1.0.0/priv/native/libliqi_sequence_diff_nif.so
```

Do not install the fixture under `contracts/native/examples`; it is schema evidence only.

## Senior 5 evidence

Required before production enablement:

- full source gate with Elixir available;
- bounded fuzz run result;
- signed ARM64 manifest verified by `verify_artifact.py`;
- direct BEAM/Rustler benchmark on OCI A1 bound to exact Git SHA and artifact checksum;
- scheduler probe with no starvation;
- feature-off and missing-artifact fallback evidence;
- corrupt/version-mismatched artifact fail-closed evidence;
- release rollback to the previous artifact without database mutation.

Until all items exist, capability status remains `source-ready` and overall state is `engineering-complete-evidence-pending`.
