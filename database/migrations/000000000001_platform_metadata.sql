CREATE SCHEMA platform AUTHORIZATION liqi_owner;
CREATE SCHEMA platform_private AUTHORIZATION liqi_owner;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA platform FROM PUBLIC;
REVOKE ALL ON SCHEMA platform_private FROM PUBLIC;

CREATE TABLE platform.schema_migrations (
    version bigint PRIMARY KEY CHECK (version > 0),
    name text NOT NULL CHECK (name ~ '^[a-z0-9_]+$'),
    checksum_sha256 character(64) NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    applied_by text NOT NULL DEFAULT session_user
);

CREATE TABLE platform.migration_runs (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    target_version bigint CHECK (target_version > 0),
    failed_version bigint CHECK (failed_version > 0),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^[a-z0-9_.-]{1,128}$'),
    runner_identity text NOT NULL DEFAULT session_user,
    CHECK (
        (status = 'running' AND finished_at IS NULL AND error_code IS NULL)
        OR (status = 'succeeded' AND finished_at IS NOT NULL AND failed_version IS NULL AND error_code IS NULL)
        OR (status = 'failed' AND finished_at IS NOT NULL AND failed_version IS NOT NULL AND error_code IS NOT NULL)
    )
);

CREATE TABLE platform.database_metadata (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    contract_version text NOT NULL,
    postgresql_major integer NOT NULL CHECK (postgresql_major = 17),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO platform.database_metadata (contract_version, postgresql_major)
VALUES ('database-v0', 17);

CREATE FUNCTION platform.current_migration_version_v0()
RETURNS bigint
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT COALESCE(MAX(version), 0) FROM platform.schema_migrations;
$$;

CREATE FUNCTION platform.database_readiness_v0(required_version bigint)
RETURNS TABLE (
    ready boolean,
    reason text,
    current_version bigint,
    expected_version bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    WITH state AS (
        SELECT
            platform.current_migration_version_v0() AS current_version,
            EXISTS (
                SELECT 1
                FROM platform.migration_runs
                WHERE status = 'failed'
                  AND started_at >= COALESCE(
                      (SELECT MAX(applied_at) FROM platform.schema_migrations),
                      '-infinity'::timestamptz
                  )
            ) AS failed_run
    )
    SELECT
        NOT failed_run AND current_version >= required_version,
        CASE
            WHEN failed_run THEN 'failed-migration-run'
            WHEN current_version < required_version THEN 'migration-pending'
            ELSE 'ready'
        END,
        current_version,
        required_version
    FROM state;
$$;

CREATE VIEW platform.database_health_v0
WITH (security_barrier = true)
AS
SELECT
    current_database() AS database_name,
    current_setting('server_version_num')::integer AS server_version_num,
    platform.current_migration_version_v0() AS migration_version,
    pg_is_in_recovery() AS in_recovery,
    clock_timestamp() AS observed_at;

REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA platform_private FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform_private FROM PUBLIC;

GRANT USAGE ON SCHEMA platform TO liqi_api, liqi_realtime, liqi_worker, liqi_readonly, liqi_monitor, liqi_backup;
GRANT EXECUTE ON FUNCTION platform.current_migration_version_v0() TO liqi_api, liqi_realtime, liqi_worker, liqi_readonly, liqi_monitor, liqi_backup;
GRANT EXECUTE ON FUNCTION platform.database_readiness_v0(bigint) TO liqi_api, liqi_realtime, liqi_worker, liqi_readonly, liqi_monitor, liqi_backup;
GRANT SELECT ON platform.database_health_v0 TO liqi_readonly, liqi_monitor, liqi_backup;
