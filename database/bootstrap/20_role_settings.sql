\set ON_ERROR_STOP on
\if :{?database_name}
\else
  \set database_name liqi
\endif

SELECT format('ALTER ROLE liqi_api IN DATABASE %I SET statement_timeout = %L', :'database_name', '5s') \gexec
SELECT format('ALTER ROLE liqi_api IN DATABASE %I SET lock_timeout = %L', :'database_name', '2s') \gexec
SELECT format('ALTER ROLE liqi_api IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '15s') \gexec
SELECT format('ALTER ROLE liqi_api IN DATABASE %I SET default_transaction_isolation = %L', :'database_name', 'read committed') \gexec
SELECT format('ALTER ROLE liqi_api IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec

SELECT format('ALTER ROLE liqi_realtime IN DATABASE %I SET statement_timeout = %L', :'database_name', '3s') \gexec
SELECT format('ALTER ROLE liqi_realtime IN DATABASE %I SET lock_timeout = %L', :'database_name', '2s') \gexec
SELECT format('ALTER ROLE liqi_realtime IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '15s') \gexec
SELECT format('ALTER ROLE liqi_realtime IN DATABASE %I SET default_transaction_isolation = %L', :'database_name', 'read committed') \gexec
SELECT format('ALTER ROLE liqi_realtime IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec

SELECT format('ALTER ROLE liqi_worker IN DATABASE %I SET statement_timeout = %L', :'database_name', '30s') \gexec
SELECT format('ALTER ROLE liqi_worker IN DATABASE %I SET lock_timeout = %L', :'database_name', '5s') \gexec
SELECT format('ALTER ROLE liqi_worker IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '30s') \gexec
SELECT format('ALTER ROLE liqi_worker IN DATABASE %I SET default_transaction_isolation = %L', :'database_name', 'read committed') \gexec
SELECT format('ALTER ROLE liqi_worker IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec

SELECT format('ALTER ROLE liqi_readonly IN DATABASE %I SET statement_timeout = %L', :'database_name', '10s') \gexec
SELECT format('ALTER ROLE liqi_readonly IN DATABASE %I SET lock_timeout = %L', :'database_name', '2s') \gexec
SELECT format('ALTER ROLE liqi_readonly IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '15s') \gexec
SELECT format('ALTER ROLE liqi_readonly IN DATABASE %I SET default_transaction_read_only = %L', :'database_name', 'on') \gexec
SELECT format('ALTER ROLE liqi_readonly IN DATABASE %I SET default_transaction_isolation = %L', :'database_name', 'read committed') \gexec
SELECT format('ALTER ROLE liqi_readonly IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec

SELECT format('ALTER ROLE liqi_migrator IN DATABASE %I SET statement_timeout = %L', :'database_name', '15min') \gexec
SELECT format('ALTER ROLE liqi_migrator IN DATABASE %I SET lock_timeout = %L', :'database_name', '30s') \gexec
SELECT format('ALTER ROLE liqi_migrator IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '60s') \gexec
SELECT format('ALTER ROLE liqi_migrator IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec

SELECT format('ALTER ROLE liqi_monitor IN DATABASE %I SET statement_timeout = %L', :'database_name', '5s') \gexec
SELECT format('ALTER ROLE liqi_monitor IN DATABASE %I SET lock_timeout = %L', :'database_name', '2s') \gexec
SELECT format('ALTER ROLE liqi_monitor IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '15s') \gexec
SELECT format('ALTER ROLE liqi_monitor IN DATABASE %I SET default_transaction_read_only = %L', :'database_name', 'on') \gexec
SELECT format('ALTER ROLE liqi_monitor IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec

SELECT format('ALTER ROLE liqi_backup IN DATABASE %I SET statement_timeout = %L', :'database_name', '30s') \gexec
SELECT format('ALTER ROLE liqi_backup IN DATABASE %I SET lock_timeout = %L', :'database_name', '5s') \gexec
SELECT format('ALTER ROLE liqi_backup IN DATABASE %I SET idle_in_transaction_session_timeout = %L', :'database_name', '30s') \gexec
SELECT format('ALTER ROLE liqi_backup IN DATABASE %I SET default_transaction_read_only = %L', :'database_name', 'on') \gexec
SELECT format('ALTER ROLE liqi_backup IN DATABASE %I SET search_path = %L', :'database_name', 'pg_catalog') \gexec
