#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
MIGRATION_DIR=${LIQI_MIGRATION_DIR:-"$ROOT_DIR/database/migrations"}
MANIFEST=${LIQI_MIGRATION_MANIFEST:-"$MIGRATION_DIR/manifest.sha256"}
PSQL=${PSQL:-psql}
LOCK_KEY_TEXT=liqi_platform:migrations:v0

require_command() {
  command -v "$1" >/dev/null 2>&1 || { echo "required command missing: $1" >&2; exit 69; }
}
require_command "$PSQL"
require_command sha256sum

: "${PGDATABASE:=liqi}"
export PGDATABASE

if [[ ! -f "$MANIFEST" ]]; then
  echo "migration manifest not found: $MANIFEST" >&2
  exit 66
fi

(
  cd "$MIGRATION_DIR"
  sha256sum --check --strict "$(basename "$MANIFEST")"
)

mapfile -t migrations < <(
  find "$MIGRATION_DIR" -maxdepth 1 -type f -name '*.sql' -printf '%f\n' \
    | grep -E '^[0-9]{12}_[a-z0-9_]+\.sql$' \
    | LC_ALL=C sort
)
if [[ ${#migrations[@]} -eq 0 ]]; then
  echo "no migrations found" >&2
  exit 65
fi

expected_manifest=$(awk '{print $2}' "$MANIFEST" | sed 's#^\*\?##' | LC_ALL=C sort)
actual_manifest=$(printf '%s\n' "${migrations[@]}")
if [[ "$expected_manifest" != "$actual_manifest" ]]; then
  echo "migration manifest and directory differ" >&2
  diff -u <(printf '%s\n' "$expected_manifest") <(printf '%s\n' "$actual_manifest") || true
  exit 65
fi

target_version_text=${migrations[${#migrations[@]}-1]%%_*}
target_version=$((10#$target_version_text))
driver=$(mktemp)
trap 'rm -f "$driver"' EXIT
chmod 600 "$driver"

{
  cat <<SQL
\\set ON_ERROR_STOP on
SELECT pg_advisory_lock(hashtextextended('$LOCK_KEY_TEXT', 0));
SET ROLE liqi_owner;
SQL
  for file_name in "${migrations[@]}"; do
    version_text=${file_name%%_*}
    version=$((10#$version_text))
    name=${file_name#*_}
    name=${name%.sql}
    checksum=$(awk -v file="$file_name" '$2 == file || $2 == "*" file {print $1}' "$MANIFEST")
    file_path=$(cd "$MIGRATION_DIR" && pwd)/$file_name
    cat <<SQL
SELECT to_regclass('platform.schema_migrations') IS NOT NULL AS registry_exists \\gset
\\if :registry_exists
SELECT EXISTS (SELECT 1 FROM platform.schema_migrations WHERE version = $version) AS migration_applied \\gset
\\else
\\set migration_applied false
\\endif
\\if :migration_applied
SELECT checksum_sha256 = '$checksum' AS checksum_matches
FROM platform.schema_migrations
WHERE version = $version
\\gset
\\if :checksum_matches
\\echo migration $version already applied
\\else
\\warn migration $version checksum mismatch
\\quit 42
\\endif
\\else
\\if :registry_exists
INSERT INTO platform.migration_runs (status, target_version)
VALUES ('running', $version)
RETURNING run_id::text AS migration_run_id
\\gset
\\else
\\unset migration_run_id
\\endif
BEGIN;
\\i $file_path
INSERT INTO platform.schema_migrations (version, name, checksum_sha256)
VALUES ($version, '$name', '$checksum');
COMMIT;
\\if :{?migration_run_id}
UPDATE platform.migration_runs
SET status = 'succeeded', finished_at = clock_timestamp()
WHERE run_id = :'migration_run_id'::uuid;
\\else
INSERT INTO platform.migration_runs (status, target_version, finished_at)
VALUES ('succeeded', $version, clock_timestamp());
\\endif
\\echo migration $version applied
\\endif
SQL
  done
  cat <<SQL
RESET ROLE;
SELECT pg_advisory_unlock(hashtextextended('$LOCK_KEY_TEXT', 0));
SQL
} > "$driver"

set +e
"$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --file="$driver"
status=$?
set -e

if [[ $status -ne 0 ]]; then
  "$PSQL" --no-psqlrc --set=ON_ERROR_STOP=1 --quiet <<SQL >/dev/null 2>&1 || true
SET ROLE liqi_owner;
DO \$\$
BEGIN
  IF to_regclass('platform.migration_runs') IS NOT NULL THEN
    UPDATE platform.migration_runs
    SET status = 'failed',
        finished_at = clock_timestamp(),
        failed_version = target_version,
        error_code = 'migration.psql_exit_$status'
    WHERE status = 'running';
  END IF;
END
\$\$;
SQL
  echo "migration run failed with exit code $status" >&2
  exit "$status"
fi

LIQI_REQUIRED_MIGRATION_VERSION=$target_version "$ROOT_DIR/database/bin/migration-status.sh"
