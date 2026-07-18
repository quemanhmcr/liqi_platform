#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LABEL=${1:-}; OUTPUT_DIR=${2:-}
[[ "$LABEL" =~ ^[0-9]{8}-[0-9]{6}[FDI](?:_[0-9]{8}-[0-9]{6}[DI])?$ ]] || { echo 'usage: fetch-backup-metadata.sh <backup-label> <output-directory>' >&2; exit 64; }
[[ -n "$OUTPUT_DIR" ]] || { echo 'output directory is required' >&2; exit 64; }
PGBACKREST_COMMAND=${LIQI_PGBACKREST_COMMAND:-"$ROOT_DIR/database/bin/pgbackrest-command.sh"}
[[ -x "$PGBACKREST_COMMAND" ]] || { echo 'pgBackRest command boundary unavailable' >&2; exit 69; }
mkdir -p "$OUTPUT_DIR"; chmod 700 "$OUTPUT_DIR"
temporary=$(mktemp); trap 'rm -f "$temporary"' EXIT
"$PGBACKREST_COMMAND" --stanza=liqi --output=json info > "$temporary"
metadata="$OUTPUT_DIR/$LABEL.json"
[[ ! -e "$metadata" && ! -e "$metadata.sha256" ]] || { echo 'metadata target already exists' >&2; exit 65; }
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" reconstruct-metadata --info "$temporary" --label "$LABEL" --output "$metadata" >/dev/null
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-metadata --metadata "$metadata" --checksum "$metadata.sha256"
