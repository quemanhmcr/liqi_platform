#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
MAX_TOTAL_TIME=${LIQI_FUZZ_SECONDS:-60}
FUZZ_TOOLCHAIN=${LIQI_FUZZ_TOOLCHAIN:-nightly}
if [[ ! "$MAX_TOTAL_TIME" =~ ^[0-9]+$ ]] || (( MAX_TOTAL_TIME < 1 || MAX_TOTAL_TIME > 3600 )); then
  printf 'LIQI_FUZZ_SECONDS must be an integer from 1 through 3600\n' >&2
  exit 64
fi
if [[ "$(uname -s)" != "Linux" ]]; then
  printf '%s\n' '{"validation":"native-fuzz-v1","status":"blocked","reason":"linux-clang-toolchain-required"}' >&2
  exit 69
fi
if [[ ! "$FUZZ_TOOLCHAIN" =~ ^nightly(-[0-9]{4}-[0-9]{2}-[0-9]{2})?$ ]]; then
  printf 'LIQI_FUZZ_TOOLCHAIN must be nightly or nightly-YYYY-MM-DD\n' >&2
  exit 64
fi
if ! rustup toolchain list | grep -q "^${FUZZ_TOOLCHAIN}"; then
  printf '%s\n' '{"validation":"native-fuzz-v1","status":"blocked","reason":"rust-nightly-required"}' >&2
  exit 69
fi
if ! cargo +"$FUZZ_TOOLCHAIN" fuzz --help >/dev/null 2>&1; then
  printf '%s\n' '{"validation":"native-fuzz-v1","status":"blocked","reason":"cargo-fuzz-required"}' >&2
  exit 69
fi
cd "$ROOT_DIR"
cargo +"$FUZZ_TOOLCHAIN" fuzz run \
  --fuzz-dir "$ROOT_DIR/native/fuzz" \
  sequence_diff_parity -- \
  -max_total_time="$MAX_TOTAL_TIME" \
  -rss_limit_mb=512 \
  -timeout=2
printf '{"validation":"native-fuzz-v1","status":"passed","seconds":%s}\n' "$MAX_TOTAL_TIME"
