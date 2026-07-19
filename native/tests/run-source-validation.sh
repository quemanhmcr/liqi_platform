#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
MODE=full
if [[ "${1:-}" == "--rust-only" ]]; then
  MODE=rust-only
  shift
fi
if [[ $# -ne 0 ]]; then
  printf 'usage: %s [--rust-only]\n' "$0" >&2
  exit 64
fi

cd "$ROOT_DIR"
export PYTHONDONTWRITEBYTECODE=1
python native/tests/validate_native_source.py
python -m unittest native.tests.test_artifact_architecture native.tests.test_deployment_manifest native.tests.test_safety_gate -v
cargo +1.97.1 fmt --all -- --check
cargo +1.97.1 metadata --no-deps --format-version 1 --locked >/dev/null
cargo +1.97.1 test --locked -p liqi-sequence-diff-core -p liqi-sequence-diff-nif
cargo +1.97.1 clippy --locked -p liqi-sequence-diff-core -p liqi-sequence-diff-nif --all-targets -- -D warnings
cargo +1.97.1 check --locked --target aarch64-unknown-linux-gnu -p liqi-sequence-diff-core
ARM64_NIF_CHECK=passed
if [[ "$(uname -s)" == "Linux" ]]; then
  cargo +1.97.1 check --locked --target aarch64-unknown-linux-gnu -p liqi-sequence-diff-nif
else
  ARM64_NIF_CHECK=blocked-non-linux-host
  printf '%s\n' '{"validation":"native-arm64-check-v1","status":"blocked","reason":"linux-host-required-for-rustler-target-check"}' >&2
fi

if [[ "$MODE" == "full" ]]; then
  if ! command -v elixir >/dev/null 2>&1; then
    printf '%s\n' '{"validation":"native-source-v1","status":"blocked","reason":"elixir-toolchain-unavailable"}' >&2
    exit 69
  fi
  elixir native/elixir/test/run-reference-tests.exs
fi

printf '{"validation":"native-source-v1","status":"passed","mode":"%s","arm64_nif_check":"%s"}\n' "$MODE" "$ARM64_NIF_CHECK"
