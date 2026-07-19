#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export LIQI_NATIVE_TARGET_TRIPLE='aarch64-unknown-linux-gnu'
exec bash "$ROOT_DIR/native/scripts/build-linux-artifact.sh" "$@"
