#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
validate_common; require_command psql; require_command createdb
: "${STATE_ADMIN_SERVICE:?STATE_ADMIN_SERVICE is required}"
: "${STATE_ROLE_PASSWORD_FILE:?STATE_ROLE_PASSWORD_FILE is required}"
require_file_0600 "$STATE_ROLE_PASSWORD_FILE"
role_credential=$(cat "$STATE_ROLE_PASSWORD_FILE"); [ ${#role_credential} -ge 24 ] || fail 'state role password must be at least 24 characters'
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
python - "$tmp" "$STATE_ROLE" "$role_credential" <<'PY2'
import pathlib,sys
path,role,role_credential=sys.argv[1:]
q=lambda s:"'"+s.replace("'","''")+"'"
pathlib.Path(path).write_text(f"""DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname={q(role)}) THEN
  EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',{q(role)},{q(role_credential)});
 ELSE
  EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L',{q(role)},{q(role_credential)});
 END IF;
END $$;
""",encoding='utf-8')
PY2
chmod 600 "$tmp"
psql "service=$STATE_ADMIN_SERVICE dbname=postgres" -v ON_ERROR_STOP=1 -f "$tmp" >/dev/null
exists=$(psql "service=$STATE_ADMIN_SERVICE dbname=postgres" -Atqc "SELECT 1 FROM pg_database WHERE datname='$STATE_DATABASE'")
[ "$exists" = 1 ] || createdb --maintenance-db="service=$STATE_ADMIN_SERVICE dbname=postgres" "$STATE_DATABASE"
psql "service=$STATE_ADMIN_SERVICE dbname=$STATE_DATABASE" -v ON_ERROR_STOP=1 -v r="$STATE_ROLE" -v s="$STATE_SCHEMA" <<'SQL' >/dev/null
SELECT format('GRANT CONNECT ON DATABASE %I TO %I',current_database(),:'r') \gexec
SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I',:'s',:'r') \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I',:'r') \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA %I TO %I',:'s',:'r') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path=%I,public',:'r',current_database(),:'s') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET statement_timeout=%L',:'r',current_database(),'15min') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET lock_timeout=%L',:'r',current_database(),'60s') \gexec
SQL
printf '{"status":"ready-for-first-tofu-init","database":"%s","schema":"%s","role":"%s"}
' "$STATE_DATABASE" "$STATE_SCHEMA" "$STATE_ROLE"
