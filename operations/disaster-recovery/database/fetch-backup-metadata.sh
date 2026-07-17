#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
LABEL=${1:-}
OUTPUT_DIR=${2:-}
[[ "$LABEL" =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]] || { echo 'usage: fetch-backup-metadata.sh <backup-label> <output-directory>' >&2; exit 64; }
[[ -n "$OUTPUT_DIR" ]] || { echo 'output directory is required' >&2; exit 64; }
: "${LIQI_OCI_OBJECT_NAMESPACE:?LIQI_OCI_OBJECT_NAMESPACE is required}"
: "${LIQI_DATABASE_BACKUP_BUCKET:?LIQI_DATABASE_BACKUP_BUCKET is required}"
OCI=${OCI:-oci}
OBJECT_PREFIX=${LIQI_BACKUP_METADATA_OBJECT_PREFIX:-postgresql/v0/metadata}
command -v "$OCI" >/dev/null 2>&1 || { echo 'OCI CLI unavailable' >&2; exit 69; }
mkdir -p "$OUTPUT_DIR"
metadata="$OUTPUT_DIR/$LABEL.json"
checksum="$metadata.sha256"

# Fetch checksum first. Metadata without a matching sidecar is never accepted.
"$OCI" os object get --auth instance_principal \
  --namespace-name "$LIQI_OCI_OBJECT_NAMESPACE" \
  --bucket-name "$LIQI_DATABASE_BACKUP_BUCKET" \
  --name "$OBJECT_PREFIX/$LABEL.json.sha256" \
  --file "$checksum" >/dev/null
"$OCI" os object get --auth instance_principal \
  --namespace-name "$LIQI_OCI_OBJECT_NAMESPACE" \
  --bucket-name "$LIQI_DATABASE_BACKUP_BUCKET" \
  --name "$OBJECT_PREFIX/$LABEL.json" \
  --file "$metadata" >/dev/null
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-metadata \
  --metadata "$metadata" --checksum "$checksum"
