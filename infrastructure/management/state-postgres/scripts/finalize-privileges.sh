#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command psql
: "${STATE_ADMIN_SERVICE:?STATE_ADMIN_SERVICE is required}"
table=$(psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -Atqc "SELECT to_regclass('$STATE_SCHEMA.states') IS NOT NULL")
[ "$table" = t ] || fail 'OpenTofu state table is absent; run the first tofu init before finalization'
psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -v ON_ERROR_STOP=1 -v r="$STATE_ROLE" -v s="$STATE_SCHEMA" <<'SQL' >/dev/null
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I',:'r') \gexec
SELECT format('REVOKE CREATE ON SCHEMA %I FROM %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE ON SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I',:'r') \gexec
SQL
printf '{"status":"finalized","schema":"%s"}
' "$STATE_SCHEMA"
