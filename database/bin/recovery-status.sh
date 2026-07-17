#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PROVIDER_ENV_FILE=${LIQI_DATABASE_PROVIDER_ENV_FILE:-/etc/liqi/database/provider.env}
ENVIRONMENT=${LIQI_ENVIRONMENT:-}
OUTPUT=${LIQI_RECOVERY_STATUS_OUTPUT:-}
METADATA=${LIQI_BACKUP_METADATA_FILE:-/var/lib/liqi/postgresql/backup-staging/metadata/latest.json}
RESTORE_RESULT=${LIQI_RESTORE_RESULT_FILE:-/var/lib/liqi/postgresql/backup-staging/restore/latest/restore-result.json}
BACKUP_STATUS=${LIQI_BACKUP_STATUS_FILE:-/var/lib/liqi/postgresql/backup-staging/metadata/backup-status-v0.json}
RESTORE_SOURCE_METADATA=${LIQI_RESTORE_SOURCE_METADATA_FILE:-}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || { echo '--output requires a path' >&2; exit 64; }
      OUTPUT=$2
      shift 2
      ;;
    --environment)
      [[ $# -ge 2 ]] || { echo '--environment requires a value' >&2; exit 64; }
      ENVIRONMENT=$2
      shift 2
      ;;
    --metadata)
      [[ $# -ge 2 ]] || { echo '--metadata requires a path' >&2; exit 64; }
      METADATA=$2
      shift 2
      ;;
    --restore-result)
      [[ $# -ge 2 ]] || { echo '--restore-result requires a path' >&2; exit 64; }
      RESTORE_RESULT=$2
      shift 2
      ;;
    --backup-status)
      [[ $# -ge 2 ]] || { echo '--backup-status requires a path' >&2; exit 64; }
      BACKUP_STATUS=$2
      shift 2
      ;;
    --restore-source-metadata)
      [[ $# -ge 2 ]] || { echo '--restore-source-metadata requires a path' >&2; exit 64; }
      RESTORE_SOURCE_METADATA=$2
      shift 2
      ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

if [[ -z "$ENVIRONMENT" ]]; then
  [[ -r "$PROVIDER_ENV_FILE" ]] || { echo "database provider environment file unavailable: $PROVIDER_ENV_FILE" >&2; exit 78; }
  ENVIRONMENT=$(awk -F= '
    $1 == "LIQI_ENVIRONMENT" {
      value = substr($0, index($0, "=") + 1)
      gsub(/\r$/, "", value)
      print value
      found = 1
    }
    END { if (!found) exit 1 }
  ' "$PROVIDER_ENV_FILE") || { echo 'LIQI_ENVIRONMENT missing from provider environment file' >&2; exit 78; }
fi
case "$ENVIRONMENT" in development|staging|production) ;; *) echo "invalid LIQI_ENVIRONMENT: $ENVIRONMENT" >&2; exit 64 ;; esac

METADATA_CHECKSUM=${LIQI_BACKUP_METADATA_CHECKSUM_FILE:-$METADATA.sha256}
RESTORE_CHECKSUM=${LIQI_RESTORE_RESULT_CHECKSUM_FILE:-$RESTORE_RESULT.sha256}
BACKUP_STATUS_CHECKSUM=${LIQI_BACKUP_STATUS_CHECKSUM_FILE:-$BACKUP_STATUS.sha256}
for file in "$METADATA" "$METADATA_CHECKSUM" "$RESTORE_RESULT" "$RESTORE_CHECKSUM" "$BACKUP_STATUS" "$BACKUP_STATUS_CHECKSUM"; do
  [[ -r "$file" ]] || { echo "required recovery evidence unavailable: $file" >&2; exit 66; }
done

args=(
  create-recovery-status
  --environment "$ENVIRONMENT"
  --metadata "$METADATA"
  --metadata-checksum "$METADATA_CHECKSUM"
  --restore-result "$RESTORE_RESULT"
  --restore-checksum "$RESTORE_CHECKSUM"
  --backup-status "$BACKUP_STATUS"
  --backup-status-checksum "$BACKUP_STATUS_CHECKSUM"
)
if [[ -n "$RESTORE_SOURCE_METADATA" ]]; then
  args+=(
    --restore-source-metadata "$RESTORE_SOURCE_METADATA"
    --restore-source-metadata-checksum "${LIQI_RESTORE_SOURCE_METADATA_CHECKSUM_FILE:-$RESTORE_SOURCE_METADATA.sha256}"
  )
fi
[[ -z "${LIQI_BACKUP_EVIDENCE_REF:-}" ]] || args+=(--backup-evidence-ref "$LIQI_BACKUP_EVIDENCE_REF")
[[ -z "${LIQI_RESTORE_EVIDENCE_REF:-}" ]] || args+=(--restore-evidence-ref "$LIQI_RESTORE_EVIDENCE_REF")
[[ -z "${LIQI_WAL_EVIDENCE_REF:-}" ]] || args+=(--wal-evidence-ref "$LIQI_WAL_EVIDENCE_REF")
[[ -z "$OUTPUT" ]] || args+=(--output "$OUTPUT")
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" "${args[@]}"
