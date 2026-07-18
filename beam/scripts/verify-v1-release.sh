#!/usr/bin/env bash
set -euo pipefail
manifest=""; output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) manifest="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$manifest" && -n "$output" ]] || { echo "--manifest and --output are required" >&2; exit 64; }
exec python beam/scripts/run_bounded.py --timeout 900 -- \
  python beam/scripts/validate_release_manifest.py --manifest "$manifest" --output "$output"
