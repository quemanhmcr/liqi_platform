CREATE TABLE platform.database_runtime_contract_v1 (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    contract_version text NOT NULL CHECK (contract_version = 'database-runtime-v1'),
    required_migration_version bigint NOT NULL CHECK (required_migration_version = 8),
    required_oban_migration_version integer NOT NULL CHECK (required_oban_migration_version = 14),
    outbox_retention interval NOT NULL CHECK (outbox_retention = interval '30 days'),
    realtime_retention interval NOT NULL CHECK (realtime_retention = interval '7 days'),
    idempotency_retention interval NOT NULL CHECK (idempotency_retention = interval '30 days'),
    installed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO platform.database_runtime_contract_v1 (
    singleton,
    contract_version,
    required_migration_version,
    required_oban_migration_version,
    outbox_retention,
    realtime_retention,
    idempotency_retention
)
VALUES (
    true,
    'database-runtime-v1',
    8,
    14,
    interval '30 days',
    interval '7 days',
    interval '30 days'
);

CREATE FUNCTION platform.current_oban_migration_version_v1()
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT COALESCE(
        NULLIF(
            pg_catalog.obj_description(
                pg_catalog.to_regclass('oban.oban_jobs'),
                'pg_class'
            ),
            ''
        )::integer,
        0
    );
$$;

CREATE FUNCTION platform.database_readiness_v1(
    required_version bigint DEFAULT 8,
    required_oban_version integer DEFAULT 14
)
RETURNS TABLE (
    ready boolean,
    write_ready boolean,
    reason text,
    current_version bigint,
    expected_version bigint,
    oban_migration_version integer,
    expected_oban_migration_version integer,
    in_recovery boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    WITH state AS (
        SELECT
            platform.current_migration_version_v0() AS current_version,
            platform.current_oban_migration_version_v1() AS oban_migration_version,
            pg_is_in_recovery() AS in_recovery,
            EXISTS (
                SELECT 1
                FROM platform.database_runtime_contract_v1 contract
                WHERE contract.singleton
                  AND contract.required_migration_version = required_version
                  AND contract.required_oban_migration_version = required_oban_version
            ) AS contract_present,
            EXISTS (
                SELECT 1
                FROM platform.migration_runs run
                WHERE run.status = 'failed'
                  AND run.started_at >= COALESCE(
                      (SELECT MAX(migration.applied_at) FROM platform.schema_migrations migration),
                      '-infinity'::timestamptz
                  )
            ) AS failed_run
    )
    SELECT
        NOT state.failed_run
            AND state.current_version >= required_version
            AND state.oban_migration_version >= required_oban_version
            AND state.contract_present,
        NOT state.failed_run
            AND state.current_version >= required_version
            AND state.oban_migration_version >= required_oban_version
            AND state.contract_present
            AND NOT state.in_recovery,
        CASE
            WHEN state.failed_run THEN 'failed-migration-run'
            WHEN state.current_version < required_version THEN 'migration-pending'
            WHEN state.oban_migration_version < required_oban_version THEN 'oban-migration-pending'
            WHEN NOT state.contract_present THEN 'contract-missing'
            WHEN state.in_recovery THEN 'database-in-recovery'
            ELSE 'ready'
        END,
        state.current_version,
        required_version,
        state.oban_migration_version,
        required_oban_version,
        state.in_recovery
    FROM state;
$$;

CREATE VIEW platform.outbox_health_v1
WITH (security_barrier = true)
AS
SELECT
    count(*) FILTER (WHERE state = 'pending') AS pending_count,
    count(*) FILTER (WHERE state = 'processing') AS processing_count,
    count(*) FILTER (WHERE state = 'succeeded') AS succeeded_count,
    count(*) FILTER (WHERE state = 'dead_letter') AS dead_letter_count,
    count(*) FILTER (WHERE protocol_version = 0) AS protocol_v0_count,
    count(*) FILTER (WHERE protocol_version = 1) AS protocol_v1_count,
    COALESCE(
        EXTRACT(EPOCH FROM (clock_timestamp() - min(created_at) FILTER (WHERE state = 'pending'))),
        0
    )::double precision AS oldest_pending_seconds,
    COALESCE(
        EXTRACT(EPOCH FROM (clock_timestamp() - min(dead_lettered_at) FILTER (WHERE state = 'dead_letter'))),
        0
    )::double precision AS oldest_dead_letter_seconds,
    clock_timestamp() AS observed_at
FROM platform.outbox_events;

CREATE VIEW platform.realtime_handoff_health_v1
WITH (security_barrier = true)
AS
SELECT
    state.last_handoff_id,
    state.retained_after_handoff_id,
    count(handoff.handoff_id) AS retained_count,
    COALESCE(
        EXTRACT(EPOCH FROM (clock_timestamp() - min(handoff.recorded_at))),
        0
    )::double precision AS oldest_retained_seconds,
    clock_timestamp() AS observed_at
FROM platform.realtime_handoff_state_v1 state
LEFT JOIN platform.realtime_handoff_events_v1 handoff ON true
WHERE state.singleton
GROUP BY state.last_handoff_id, state.retained_after_handoff_id;

CREATE VIEW platform.oban_health_v1
WITH (security_barrier = true)
AS
SELECT
    count(*) FILTER (WHERE state = 'available') AS available_count,
    count(*) FILTER (WHERE state = 'scheduled') AS scheduled_count,
    count(*) FILTER (WHERE state = 'retryable') AS retryable_count,
    count(*) FILTER (WHERE state = 'executing') AS executing_count,
    count(*) FILTER (WHERE state = 'discarded') AS discarded_count,
    count(*) FILTER (WHERE state = 'cancelled') AS cancelled_count,
    COALESCE(
        EXTRACT(EPOCH FROM (
            timezone('UTC', clock_timestamp())
            - min(scheduled_at) FILTER (WHERE state IN ('available', 'scheduled', 'retryable'))
        )),
        0
    )::double precision AS oldest_runnable_seconds,
    clock_timestamp() AS observed_at
FROM oban.oban_jobs;

CREATE FUNCTION platform.prune_command_idempotency_v1(
    completed_before timestamptz,
    requested_batch_size integer DEFAULT 500
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    deleted_count integer;
BEGIN
    IF completed_before IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'completed_before is required';
    END IF;
    IF requested_batch_size IS NULL OR requested_batch_size < 1 OR requested_batch_size > 500 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'batch size must be between 1 and 500';
    END IF;

    WITH candidates AS (
        SELECT record.scope, record.idempotency_key
        FROM platform.command_idempotency_v1 record
        WHERE record.completed_at < completed_before
          AND record.expires_at <= clock_timestamp()
        ORDER BY record.expires_at, record.scope, record.idempotency_key
        LIMIT requested_batch_size
        FOR UPDATE SKIP LOCKED
    )
    DELETE FROM platform.command_idempotency_v1 record
    USING candidates
    WHERE record.scope = candidates.scope
      AND record.idempotency_key = candidates.idempotency_key;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;

CREATE FUNCTION platform.prune_outbox_v1(
    succeeded_before timestamptz,
    dead_letter_before timestamptz,
    requested_batch_size integer DEFAULT 500
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    candidate_event_ids uuid[];
    deleted_count integer;
BEGIN
    IF succeeded_before IS NULL OR dead_letter_before IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'terminal retention cutoffs are required';
    END IF;
    IF requested_batch_size IS NULL OR requested_batch_size < 1 OR requested_batch_size > 500 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'batch size must be between 1 and 500';
    END IF;

    SELECT COALESCE(array_agg(candidate.event_id), ARRAY[]::uuid[])
    INTO candidate_event_ids
    FROM (
        SELECT event.event_id
        FROM platform.outbox_events event
        WHERE (
                (event.state = 'succeeded' AND event.completed_at < succeeded_before)
                OR (event.state = 'dead_letter' AND event.dead_lettered_at < dead_letter_before)
            )
          AND NOT EXISTS (
              SELECT 1 FROM platform.command_idempotency_v1 record
              WHERE record.event_id = event.event_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM platform.probe_state_v0 probe
              WHERE probe.requested_event_id = event.event_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM platform.probe_effects_v0 effect
              WHERE effect.event_id = event.event_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM platform.realtime_handoff_events_v0 handoff
              WHERE handoff.event_id = event.event_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM platform.realtime_handoff_events_v1 handoff
              WHERE handoff.event_id = event.event_id
          )
        ORDER BY COALESCE(event.completed_at, event.dead_lettered_at), event.event_id
        LIMIT requested_batch_size
        FOR UPDATE SKIP LOCKED
    ) candidate;

    IF cardinality(candidate_event_ids) = 0 THEN
        RETURN 0;
    END IF;

    DELETE FROM platform.outbox_attempts attempt
    WHERE attempt.event_id = ANY(candidate_event_ids);

    DELETE FROM platform.outbox_events event
    WHERE event.event_id = ANY(candidate_event_ids);

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;

CREATE FUNCTION platform.backup_verification_state_v1()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT jsonb_build_object(
        'database', current_database(),
        'postgresqlVersion', current_setting('server_version'),
        'postgresqlMajor', current_setting('server_version_num')::integer / 10000,
        'migrationVersion', platform.current_migration_version_v0(),
        'requiredMigrationVersion', contract.required_migration_version,
        'obanMigrationVersion', platform.current_oban_migration_version_v1(),
        'requiredObanMigrationVersion', contract.required_oban_migration_version,
        'inRecovery', pg_is_in_recovery(),
        'failedMigrationRuns', (
            SELECT count(*)
            FROM platform.migration_runs run
            WHERE run.status = 'failed'
        ),
        'outbox', (
            SELECT to_jsonb(health) - 'observed_at'
            FROM platform.outbox_health_v1 health
        ),
        'realtimeHandoff', (
            SELECT to_jsonb(health) - 'observed_at'
            FROM platform.realtime_handoff_health_v1 health
        ),
        'oban', (
            SELECT to_jsonb(health) - 'observed_at'
            FROM platform.oban_health_v1 health
        ),
        'probe', (
            SELECT jsonb_build_object(
                'probeId', state.probe_id,
                'eventId', state.requested_event_id,
                'aggregateVersion', state.aggregate_version,
                'probeStatus', state.status,
                'outboxState', event.state,
                'effectCount', count(effect.event_id),
                'completedAt', state.completed_at
            )
            FROM platform.probe_state_v0 state
            JOIN platform.outbox_events event
              ON event.event_id = state.requested_event_id
            LEFT JOIN platform.probe_effects_v0 effect
              ON effect.event_id = state.requested_event_id
            WHERE state.status = 'completed'
            GROUP BY
                state.probe_id,
                state.requested_event_id,
                state.aggregate_version,
                state.status,
                state.completed_at,
                event.state
            ORDER BY state.completed_at DESC
            LIMIT 1
        )
    )
    FROM platform.database_runtime_contract_v1 contract
    WHERE contract.singleton;
$$;

REVOKE ALL ON platform.database_runtime_contract_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.current_oban_migration_version_v1() FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.database_readiness_v1(bigint, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.prune_command_idempotency_v1(timestamptz, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.prune_outbox_v1(timestamptz, timestamptz, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.backup_verification_state_v1() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION platform.current_oban_migration_version_v1()
    TO liqi_api, liqi_realtime, liqi_worker, liqi_readonly, liqi_monitor, liqi_backup;
GRANT EXECUTE ON FUNCTION platform.database_readiness_v1(bigint, integer)
    TO liqi_api, liqi_realtime, liqi_worker, liqi_readonly, liqi_monitor, liqi_backup;
GRANT EXECUTE ON FUNCTION platform.prune_command_idempotency_v1(timestamptz, integer)
    TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.prune_outbox_v1(timestamptz, timestamptz, integer)
    TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.backup_verification_state_v1()
    TO liqi_backup, liqi_monitor;

GRANT SELECT ON platform.outbox_health_v1, platform.realtime_handoff_health_v1, platform.oban_health_v1
    TO liqi_monitor, liqi_backup;

COMMENT ON FUNCTION platform.database_readiness_v1(bigint, integer) IS
    'Runtime readiness for required platform and Oban migrations. write_ready is false on a recovery standby.';
COMMENT ON FUNCTION platform.prune_outbox_v1(timestamptz, timestamptz, integer) IS
    'Bounded terminal outbox pruning. Authority/projection/idempotency references prevent deletion.';
COMMENT ON FUNCTION platform.backup_verification_state_v1() IS
    'Backup and isolated-restore invariant projection for the V1 database and durable work plane.';
