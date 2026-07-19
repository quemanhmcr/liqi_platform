#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command psql
: "${STATE_FINALIZE_SERVICE:?STATE_FINALIZE_SERVICE is required for one-time ownership transfer}"
STATE_OWNER_ROLE=${STATE_OWNER_ROLE:-liqi_state_admin}
validate_identifier "$STATE_OWNER_ROLE"
is_super=$(psql "service=$STATE_FINALIZE_SERVICE dbname=$STATE_DATABASE" -Atqc "SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
[ "$is_super" = t ] || fail 'STATE_FINALIZE_SERVICE must be the protected local bootstrap superuser for one-time ownership transfer'
table=$(psql "service=$STATE_FINALIZE_SERVICE dbname=$STATE_DATABASE" -Atqc "SELECT to_regclass('$STATE_SCHEMA.states') IS NOT NULL")
[ "$table" = t ] || fail 'OpenTofu state table is absent; run the first tofu init before finalization'
sequence=$(psql "service=$STATE_FINALIZE_SERVICE dbname=$STATE_DATABASE" -Atqc "SELECT to_regclass('public.global_states_id_seq') IS NOT NULL")
[ "$sequence" = t ] || fail 'OpenTofu global state sequence is absent; run the first tofu init before finalization'
psql "service=$STATE_FINALIZE_SERVICE dbname=$STATE_DATABASE" -v ON_ERROR_STOP=1 -v r="$STATE_ROLE" -v s="$STATE_SCHEMA" -v o="$STATE_OWNER_ROLE" <<'SQL' >/dev/null
BEGIN;
-- The protected local bootstrap superuser is used only here because PostgreSQL
-- ownership transfer to an independent non-superuser role cannot be performed
-- by a CREATEROLE role that is not itself the old owner. Runtime operations do
-- not use this service after finalization.
SELECT format('ALTER SCHEMA %I OWNER TO %I',:'s',:'o') \gexec
SELECT format('ALTER TABLE %I.states OWNER TO %I',:'s',:'o') \gexec
SELECT format('ALTER SEQUENCE public.global_states_id_seq OWNER TO %I',:'o') \gexec
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I',:'r') \gexec
SELECT format('REVOKE CREATE ON SCHEMA %I FROM %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE ON SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE,SELECT,UPDATE ON SEQUENCE public.global_states_id_seq TO %I',:'r') \gexec
COMMIT;
SQL
printf '{"status":"finalized","schema":"%s","owner_role":"%s"}
' "$STATE_SCHEMA" "$STATE_OWNER_ROLE"
