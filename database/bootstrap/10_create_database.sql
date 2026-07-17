\set ON_ERROR_STOP on
\if :{?database_name}
\else
  \set database_name liqi
\endif

SELECT format('CREATE DATABASE %I OWNER liqi_owner TEMPLATE template0 ENCODING %L LC_COLLATE %L LC_CTYPE %L',
              :'database_name', 'UTF8', 'C.UTF-8', 'C.UTF-8')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database_name')
\gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'database_name')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO liqi_migrator, liqi_api, liqi_realtime, liqi_worker, liqi_readonly, liqi_monitor, liqi_backup', :'database_name')
\gexec
