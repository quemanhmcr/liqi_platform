#!/usr/bin/env bash
set -euo pipefail
seconds=${1:-20}; [[ "$seconds" =~ ^[0-9]+$ ]] || exit 2; sleep "$seconds"
