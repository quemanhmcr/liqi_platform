#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE_ID=${LIQI_RELEASE_ID:-}
SOURCE_REVISION=${LIQI_SOURCE_REVISION:-}
BUILD_JOBS=${LIQI_NATIVE_BUILD_JOBS:-2}
OUTPUT_DIR=${LIQI_NATIVE_OUTPUT_DIR:-}

if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  printf 'LIQI_RELEASE_ID is required and must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}\n' >&2
  exit 64
fi
if [[ ! "$BUILD_JOBS" =~ ^[0-9]+$ ]] || (( BUILD_JOBS < 1 || BUILD_JOBS > 2 )); then
  printf 'LIQI_NATIVE_BUILD_JOBS must be 1 or 2 for the A1 capacity envelope\n' >&2
  exit 64
fi
if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  printf '%s\n' '{"artifact":"liqi_sequence_diff_nif","status":"blocked","reason":"linux-aarch64-builder-required"}' >&2
  exit 69
fi
for command in cargo rustc git readelf sha256sum python; do
  command -v "$command" >/dev/null 2>&1 || { printf 'missing required command: %s\n' "$command" >&2; exit 69; }
done
if [[ "$(rustc --version)" != "rustc 1.97.1 "* ]]; then
  printf 'rustc 1.97.1 is required, got: %s\n' "$(rustc --version)" >&2
  exit 69
fi
if [[ "$(cargo --version)" != "cargo 1.97.1 "* ]]; then
  printf 'cargo 1.97.1 is required, got: %s\n' "$(cargo --version)" >&2
  exit 69
fi
cd "$ROOT_DIR"
if [[ -z "$SOURCE_REVISION" ]]; then
  SOURCE_REVISION=$(git rev-parse HEAD)
fi
if [[ ! "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'LIQI_SOURCE_REVISION must be an exact 40-character Git SHA\n' >&2
  exit 64
fi
if [[ "$(git rev-parse HEAD)" != "$SOURCE_REVISION" ]]; then
  printf 'checked-out SHA does not match LIQI_SOURCE_REVISION\n' >&2
  exit 65
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  printf 'artifact builds require a clean tracked worktree\n' >&2
  exit 65
fi

MEM_AVAILABLE_KIB=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
DISK_AVAILABLE_KIB=$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')
if (( MEM_AVAILABLE_KIB < 2097152 )); then
  printf 'at least 2 GiB available memory is required before native build\n' >&2
  exit 69
fi
if (( DISK_AVAILABLE_KIB < 4194304 )); then
  printf 'at least 4 GiB available disk is required before native build\n' >&2
  exit 69
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$ROOT_DIR/.artifacts/native/$RELEASE_ID"
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
ARTIFACT_NAME=libliqi_sequence_diff_nif.so
BUILT_ARTIFACT="$ROOT_DIR/target/aarch64-unknown-linux-gnu/nif-release/$ARTIFACT_NAME"
OUTPUT_ARTIFACT="$OUTPUT_DIR/$ARTIFACT_NAME"

export CARGO_INCREMENTAL=0
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git show -s --format=%ct "$SOURCE_REVISION")}
export RUSTFLAGS="${RUSTFLAGS:-} --remap-path-prefix=$ROOT_DIR=/workspace/liqi_platform"

cargo +1.97.1 build \
  --locked \
  --profile nif-release \
  --target aarch64-unknown-linux-gnu \
  -p liqi-sequence-diff-nif \
  -j "$BUILD_JOBS"

[[ -f "$BUILT_ARTIFACT" ]] || { printf 'expected artifact not found: %s\n' "$BUILT_ARTIFACT" >&2; exit 66; }
readelf -h "$BUILT_ARTIFACT" | grep -Eq 'Machine:[[:space:]]+AArch64' || {
  printf 'built artifact is not ELF AArch64\n' >&2
  exit 65
}
install -m 0755 "$BUILT_ARTIFACT" "$OUTPUT_ARTIFACT"
sha256sum "$OUTPUT_ARTIFACT" > "$OUTPUT_ARTIFACT.sha256"

python - "$OUTPUT_ARTIFACT" "$SOURCE_REVISION" "$RELEASE_ID" <<'PY'
import hashlib,json,sys
from pathlib import Path
artifact=Path(sys.argv[1])
print(json.dumps({
  "artifact":"liqi_sequence_diff_nif",
  "status":"built-unsigned",
  "release_id":sys.argv[3],
  "source_revision":sys.argv[2],
  "target_triple":"aarch64-unknown-linux-gnu",
  "path":str(artifact),
  "sha256":hashlib.sha256(artifact.read_bytes()).hexdigest(),
  "size_bytes":artifact.stat().st_size,
},sort_keys=True,separators=(",",":")))
PY
