#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
result="$temporary/restore-result.json"
cp "$ROOT_DIR/contracts/platform/database-restore-result-v0.example.json" "$result"
checksum=$(sha256sum "$result" | awk '{print $1}')
printf '%s  %s\n' "$checksum" "$(basename "$result")" > "$result.sha256"
LIQI_RESTORE_RESULT_FILE="$result" "$ROOT_DIR/database/bin/restore-result-metrics.sh" > "$temporary/metrics"
grep -qx 'liqi_database_restore_verification_success 1' "$temporary/metrics"
grep -qx 'liqi_database_restore_duration_seconds 720.0' "$temporary/metrics"

printf ' ' >> "$result"
set +e
LIQI_RESTORE_RESULT_FILE="$result" "$ROOT_DIR/database/bin/restore-result-metrics.sh" >/dev/null 2>&1
status=$?
set -e
[[ "$status" -ne 0 ]] || { echo 'corrupt restore result was accepted' >&2; exit 1; }
printf '%s\n' '{"validation":"database-restore-result-metrics-v0","passed":true}'
