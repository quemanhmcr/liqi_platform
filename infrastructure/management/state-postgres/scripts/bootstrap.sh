#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command psql; require_command createdb
: "${STATE_ADMIN_SERVICE:?STATE_ADMIN_SERVICE is required}"
: "${STATE_ROLE_PASSWORD_FILE:?STATE_ROLE_PASSWORD_FILE is required}"
require_file_0600 "$STATE_ROLE_PASSWORD_FILE"
role_credential=$(cat "$STATE_ROLE_PASSWORD_FILE"); [ ${#role_credential} -ge 24 ] || fail 'state role password must be at least 24 characters'
if [ -n "${STATE_RUNTIME_PASSFILE:-}" ]; then
  runtime_host=${STATE_RUNTIME_HOST:-Admin.localdomain}
  runtime_port=${STATE_RUNTIME_PORT:-55432}
  printf '%s:%s:%s:%s:%s\n' "$runtime_host" "$runtime_port" "$STATE_DATABASE" "$STATE_ROLE" "$role_credential" > "$STATE_RUNTIME_PASSFILE"
  protect_file "$STATE_RUNTIME_PASSFILE" 600
fi
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
python - "$tmp" "$STATE_ROLE" "$role_credential" <<'PY2'
import pathlib,sys
path,role,role_credential=sys.argv[1:]
q=lambda s:"'"+s.replace("'","''")+"'"
pathlib.Path(path).write_text(f"""DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname={q(role)}) THEN
  EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',{q(role)},{q(role_credential)});
 ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname={q(role)} AND (rolsuper OR rolreplication)) THEN
  RAISE EXCEPTION 'state runtime role must never be superuser or replication-capable';
 ELSE
  -- PostgreSQL 17 permits CREATEROLE to manage ordinary role attributes, but
  -- only a superuser may restate SUPERUSER/REPLICATION flags on ALTER ROLE.
  EXECUTE format('ALTER ROLE %I LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',{q(role)},{q(role_credential)});
 END IF;
END $$;
""",encoding='utf-8')
PY2
protect_file "$tmp" 600
psql "service=$STATE_ADMIN_SERVICE dbname=postgres" -v ON_ERROR_STOP=1 -f "$tmp" >/dev/null
exists=$(psql "service=$STATE_ADMIN_SERVICE dbname=postgres" -Atqc "SELECT 1 FROM pg_database WHERE datname='$STATE_DATABASE'")
[ "$exists" = 1 ] || createdb --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" "$STATE_DATABASE"
psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -v ON_ERROR_STOP=1 -v r="$STATE_ROLE" -v s="$STATE_SCHEMA" <<'SQL' >/dev/null
BEGIN;
-- PostgreSQL 16+ separates ADMIN, SET and INHERIT membership options. The
-- CREATEROLE bootstrap admin receives ADMIN but not SET when it creates the
-- runtime role. Grant SET only for this ownership transaction and revoke it
-- before commit; the runtime remains NOINHERIT and the admin is not retained
-- as a member of the state role.
SELECT format('GRANT %I TO %I WITH SET TRUE, INHERIT FALSE',:'r',current_user) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I',current_database(),:'r') \gexec
SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I',:'s',:'r') \gexec
SELECT CASE WHEN pg_get_userbyid(nspowner)=:'r' THEN 1 ELSE 1/0 END
FROM pg_namespace WHERE nspname=:'s';
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I',:'r') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path=%I,public',:'r',current_database(),:'s') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET statement_timeout=%L',:'r',current_database(),'15min') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET lock_timeout=%L',:'r',current_database(),'60s') \gexec
SELECT format('REVOKE %I FROM %I',:'r',current_user) \gexec
COMMIT;
SQL
printf '{"status":"ready-for-first-tofu-init","database":"%s","schema":"%s","role":"%s"}
' "$STATE_DATABASE" "$STATE_SCHEMA" "$STATE_ROLE"
