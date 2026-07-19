#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE_ID=${LIQI_RELEASE_ID:-}
SOURCE_REVISION=${LIQI_SOURCE_REVISION:-}
BUILD_JOBS=${LIQI_NATIVE_BUILD_JOBS:-2}
OUTPUT_DIR=${LIQI_NATIVE_OUTPUT_DIR:-}
TARGET_TRIPLE=${LIQI_NATIVE_TARGET_TRIPLE:-}

case "$TARGET_TRIPLE" in
  aarch64-unknown-linux-gnu) REQUIRED_UNAME='aarch64'; ELF_MACHINE=183 ;;
  x86_64-unknown-linux-gnu) REQUIRED_UNAME='x86_64'; ELF_MACHINE=62 ;;
  *) printf 'LIQI_NATIVE_TARGET_TRIPLE must be selected by an approved target wrapper\n' >&2; exit 64 ;;
esac

if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  printf 'LIQI_RELEASE_ID is required and must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}\n' >&2
  exit 64
fi
if [[ ! "$BUILD_JOBS" =~ ^[0-9]+$ ]] || (( BUILD_JOBS < 1 || BUILD_JOBS > 2 )); then
  printf 'LIQI_NATIVE_BUILD_JOBS must be 1 or 2 for the 4 OCPU/24 GiB host envelope\n' >&2
  exit 64
fi
if [[ "$(uname -s)" != 'Linux' || "$(uname -m)" != "$REQUIRED_UNAME" ]]; then
  printf '{"artifact":"liqi_sequence_diff_nif","status":"blocked","reason":"linux-%s-builder-required"}\n' "$REQUIRED_UNAME" >&2
  exit 69
fi
for command in cargo rustc git sha256sum python; do
  command -v "$command" >/dev/null 2>&1 || { printf 'missing required command: %s\n' "$command" >&2; exit 69; }
done
[[ "$(rustc --version)" == 'rustc 1.97.1 '* ]] || { printf 'rustc 1.97.1 is required, got: %s\n' "$(rustc --version)" >&2; exit 69; }
[[ "$(cargo --version)" == 'cargo 1.97.1 '* ]] || { printf 'cargo 1.97.1 is required, got: %s\n' "$(cargo --version)" >&2; exit 69; }

cd "$ROOT_DIR"
[[ -n "$SOURCE_REVISION" ]] || SOURCE_REVISION=$(git rev-parse HEAD)
[[ "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]] || { printf 'LIQI_SOURCE_REVISION must be an exact 40-character Git SHA\n' >&2; exit 64; }
[[ "$(git rev-parse HEAD)" == "$SOURCE_REVISION" ]] || { printf 'checked-out SHA does not match LIQI_SOURCE_REVISION\n' >&2; exit 65; }
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || { printf 'artifact builds require a clean tracked worktree\n' >&2; exit 65; }

MEM_AVAILABLE_KIB=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
DISK_AVAILABLE_KIB=$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')
(( MEM_AVAILABLE_KIB >= 2097152 )) || { printf 'at least 2 GiB available memory is required before native build\n' >&2; exit 69; }
(( DISK_AVAILABLE_KIB >= 4194304 )) || { printf 'at least 4 GiB available disk is required before native build\n' >&2; exit 69; }

[[ -n "$OUTPUT_DIR" ]] || OUTPUT_DIR="$ROOT_DIR/.artifacts/native/$RELEASE_ID"
OUTPUT_PARENT=$(dirname "$OUTPUT_DIR")
OUTPUT_NAME=$(basename "$OUTPUT_DIR")
[[ "$OUTPUT_NAME" != '.' && "$OUTPUT_NAME" != '..' ]] || { printf 'LIQI_NATIVE_OUTPUT_DIR must name a new directory\n' >&2; exit 64; }
mkdir -p "$OUTPUT_PARENT"
OUTPUT_PARENT=$(cd "$OUTPUT_PARENT" && pwd)
OUTPUT_DIR="$OUTPUT_PARENT/$OUTPUT_NAME"
[[ ! -e "$OUTPUT_DIR" && ! -L "$OUTPUT_DIR" ]] || { printf 'native output directory already exists: %s\n' "$OUTPUT_DIR" >&2; exit 65; }
STAGING_DIR=$(mktemp -d "$OUTPUT_PARENT/.${OUTPUT_NAME}.tmp.XXXXXX")
trap 'rm -rf "$STAGING_DIR"' EXIT
ARTIFACT_NAME='libliqi_sequence_diff_nif.so'
BUILT_ARTIFACT="$ROOT_DIR/target/$TARGET_TRIPLE/nif-release/$ARTIFACT_NAME"
OUTPUT_ARTIFACT="$STAGING_DIR/$ARTIFACT_NAME"

export CARGO_INCREMENTAL=0
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git show -s --format=%ct "$SOURCE_REVISION")}
export RUSTFLAGS="${RUSTFLAGS:-} --remap-path-prefix=$ROOT_DIR=/workspace/liqi_platform"

cargo +1.97.1 build --locked --profile nif-release --target "$TARGET_TRIPLE" -p liqi-sequence-diff-nif -j "$BUILD_JOBS"
[[ -f "$BUILT_ARTIFACT" ]] || { printf 'expected artifact not found: %s\n' "$BUILT_ARTIFACT" >&2; exit 66; }
python - "$BUILT_ARTIFACT" "$ELF_MACHINE" "$TARGET_TRIPLE" <<'PY'
import struct,sys
from pathlib import Path
path=Path(sys.argv[1]); expected=int(sys.argv[2]); target=sys.argv[3]
header=path.read_bytes()[:20]
if len(header)<20 or header[:4]!=b'\x7fELF' or header[4]!=2 or header[5]!=1:
    raise SystemExit('built artifact is not little-endian ELF64')
machine=struct.unpack('<H',header[18:20])[0]
if machine!=expected:
    raise SystemExit(f'built artifact ELF machine {machine} does not match {target} ({expected})')
PY
install -m 0755 "$BUILT_ARTIFACT" "$OUTPUT_ARTIFACT"
sha256sum "$OUTPUT_ARTIFACT" > "$OUTPUT_ARTIFACT.sha256"
mv -- "$STAGING_DIR" "$OUTPUT_DIR"
trap - EXIT
FINAL_ARTIFACT="$OUTPUT_DIR/$ARTIFACT_NAME"

python - "$FINAL_ARTIFACT" "$SOURCE_REVISION" "$RELEASE_ID" "$TARGET_TRIPLE" <<'PY'
import hashlib,json,sys
from pathlib import Path
artifact=Path(sys.argv[1])
print(json.dumps({
  "artifact":"liqi_sequence_diff_nif","status":"built-unsigned","release_id":sys.argv[3],
  "source_revision":sys.argv[2],"target_triple":sys.argv[4],"path":str(artifact),
  "sha256":hashlib.sha256(artifact.read_bytes()).hexdigest(),"size_bytes":artifact.stat().st_size,
},sort_keys=True,separators=(",",":")))
PY
