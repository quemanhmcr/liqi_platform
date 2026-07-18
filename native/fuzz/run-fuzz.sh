#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
MAX_TOTAL_TIME=${LIQI_FUZZ_SECONDS:-60}
if [[ ! "$MAX_TOTAL_TIME" =~ ^[0-9]+$ ]] || (( MAX_TOTAL_TIME < 1 || MAX_TOTAL_TIME > 3600 )); then
  printf 'LIQI_FUZZ_SECONDS must be an integer from 1 through 3600\n' >&2
  exit 64
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  printf '%s\n' '{"validation":"native-fuzz-v1","status":"blocked","reason":"linux-clang-toolchain-required"}' >&2
  exit 69
fi
if ! rustup toolchain list | grep -q '^nightly'; then
  printf '%s\n' '{"validation":"native-fuzz-v1","status":"blocked","reason":"rust-nightly-required"}' >&2
  exit 69
fi
if ! cargo +nightly fuzz --help >/dev/null 2>&1; then
  printf '%s\n' '{"validation":"native-fuzz-v1","status":"blocked","reason":"cargo-fuzz-required"}' >&2
  exit 69
fi
cd "$ROOT_DIR/native/fuzz"
cargo +nightly fuzz run sequence_diff_parity -- \
  -max_total_time="$MAX_TOTAL_TIME" \
  -rss_limit_mb=512 \
  -timeout=2
printf '{"validation":"native-fuzz-v1","status":"passed","seconds":%s}\n' "$MAX_TOTAL_TIME"
