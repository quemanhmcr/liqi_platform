#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command psql
: "${STATE_ADMIN_SERVICE:?STATE_ADMIN_SERVICE is required}"
table=$(psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -Atqc "SELECT to_regclass('$STATE_SCHEMA.states') IS NOT NULL")
[ "$table" = t ] || fail 'OpenTofu state table is absent; run the first tofu init before finalization'
sequence=$(psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -Atqc "SELECT to_regclass('public.global_states_id_seq') IS NOT NULL")
[ "$sequence" = t ] || fail 'OpenTofu global state sequence is absent; run the first tofu init before finalization'
psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -v ON_ERROR_STOP=1 -v r="$STATE_ROLE" -v s="$STATE_SCHEMA" <<'SQL' >/dev/null
BEGIN;
-- Backend bootstrap creates the schema/table/sequence as the runtime role.
-- PostgreSQL owners retain implicit DDL privileges even after REVOKE, so
-- finalization must move ownership to the independent state admin. PostgreSQL
-- 16+ requires SET membership to act as the old owner; grant it only for this
-- transaction and revoke it before commit.
SELECT format('GRANT %I TO %I WITH SET TRUE, INHERIT FALSE',:'r',current_user) \gexec
SELECT format('ALTER SCHEMA %I OWNER TO %I',:'s',current_user) \gexec
SELECT format('ALTER TABLE %I.states OWNER TO %I',:'s',current_user) \gexec
SELECT format('ALTER SEQUENCE public.global_states_id_seq OWNER TO %I',current_user) \gexec
SELECT format('REVOKE %I FROM %I',:'r',current_user) \gexec
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I',:'r') \gexec
SELECT format('REVOKE CREATE ON SCHEMA %I FROM %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE ON SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE,SELECT,UPDATE ON SEQUENCE public.global_states_id_seq TO %I',:'r') \gexec
COMMIT;
SQL
printf '{"status":"finalized","schema":"%s"}
' "$STATE_SCHEMA"
