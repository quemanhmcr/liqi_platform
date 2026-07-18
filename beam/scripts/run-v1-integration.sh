#!/usr/bin/env bash
set -euo pipefail
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) output="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$output" ]] || { echo "--output is required" >&2; exit 64; }
exec python beam/scripts/run_v1_integration.py --output "$output"
