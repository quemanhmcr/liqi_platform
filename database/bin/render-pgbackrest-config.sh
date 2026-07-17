#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TEMPLATE=${LIQI_PGBACKREST_TEMPLATE:-"$ROOT_DIR/database/config/pgbackrest.conf.template"}
OUTPUT=${LIQI_PGBACKREST_CONFIG_PATH:-/etc/pgbackrest/pgbackrest.conf}

required=(
  LIQI_PGDATA
  LIQI_PGBACKREST_SPOOL_PATH
  LIQI_PGBACKREST_LOG_PATH
  LIQI_OCI_OBJECT_NAMESPACE
  LIQI_OCI_REGION
  LIQI_DATABASE_BACKUP_BUCKET
)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "required environment variable missing: $name" >&2; exit 64; }
done

export LIQI_PGBACKREST_REPO_PATH=${LIQI_PGBACKREST_REPO_PATH:-/postgresql/v0}
export LIQI_PGBACKREST_WRAPPER_PATH=${LIQI_PGBACKREST_WRAPPER_PATH:-/opt/liqi/current/database/bin/pgbackrest-command.sh}
export TEMPLATE OUTPUT
umask 077
PYTHONDONTWRITEBYTECODE=1 python - <<'PY'
import os
import re
from pathlib import Path

absolute_names = ["LIQI_PGDATA", "LIQI_PGBACKREST_SPOOL_PATH", "LIQI_PGBACKREST_LOG_PATH", "LIQI_PGBACKREST_REPO_PATH", "LIQI_PGBACKREST_WRAPPER_PATH"]
for name in absolute_names:
    value = os.environ[name]
    if not value.startswith("/") or "//" in value or value.endswith("/"):
        raise SystemExit(f"{name} must be a normalized absolute path without trailing slash")
namespace = os.environ["LIQI_OCI_OBJECT_NAMESPACE"]
region = os.environ["LIQI_OCI_REGION"]
bucket = os.environ["LIQI_DATABASE_BACKUP_BUCKET"]
if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", namespace):
    raise SystemExit("invalid OCI Object Storage namespace")
if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region):
    raise SystemExit("invalid OCI region identifier")
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
    raise SystemExit("invalid Object Storage bucket name")

template = Path(os.environ["TEMPLATE"]).read_text(encoding="utf-8")
replacements = {
    "@@PGDATA@@": os.environ["LIQI_PGDATA"],
    "@@SPOOL_PATH@@": os.environ["LIQI_PGBACKREST_SPOOL_PATH"],
    "@@LOG_PATH@@": os.environ["LIQI_PGBACKREST_LOG_PATH"],
    "@@REPO_PATH@@": os.environ["LIQI_PGBACKREST_REPO_PATH"],
    "@@NAMESPACE@@": namespace,
    "@@REGION@@": region,
    "@@BUCKET@@": bucket,
    "@@PGBACKREST_WRAPPER@@": os.environ["LIQI_PGBACKREST_WRAPPER_PATH"],
}
for token, value in replacements.items():
    template = template.replace(token, value)
if "@@" in template:
    raise SystemExit("unresolved pgBackRest template token")
for forbidden in ("repo1-s3-key=", "repo1-s3-key-secret=", "repo1-cipher-pass="):
    if forbidden in template:
        raise SystemExit(f"persistent pgBackRest config contains forbidden secret option: {forbidden}")
output = Path(os.environ["OUTPUT"])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(template, encoding="utf-8")
output.chmod(0o600)
print(output)
PY
