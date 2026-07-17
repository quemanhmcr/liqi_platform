CREATE FUNCTION platform.create_recovery_probe_v0()
RETURNS TABLE (
    probe_id uuid,
    event_id uuid,
    completed_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    new_probe_id uuid := gen_random_uuid();
    new_event_id uuid := gen_random_uuid();
    new_claim_token uuid := gen_random_uuid();
    event_time timestamptz := clock_timestamp();
    acknowledgement text;
BEGIN
    PERFORM platform.request_probe_v0(new_probe_id, new_event_id, event_time);

    UPDATE platform.outbox_events event
    SET state = 'processing',
        attempt_count = event.attempt_count + 1,
        claim_token = new_claim_token,
        claimed_by = 'database-recovery-probe-v0',
        lease_expires_at = clock_timestamp() + interval '30 seconds',
        updated_at = clock_timestamp()
    WHERE event.event_id = new_event_id
      AND event.state = 'pending'
      AND event.attempt_count < event.max_attempts;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'recovery probe outbox event could not be claimed';
    END IF;

    INSERT INTO platform.outbox_attempts (
        event_id,
        attempt_no,
        claim_token,
        consumer_id,
        claimed_at,
        lease_expires_at
    )
    SELECT
        event.event_id,
        event.attempt_count,
        event.claim_token,
        event.claimed_by,
        clock_timestamp(),
        event.lease_expires_at
    FROM platform.outbox_events event
    WHERE event.event_id = new_event_id;

    acknowledgement := platform.apply_probe_effect_and_ack_v0(
        new_event_id,
        new_claim_token,
        'database-recovery-probe-v0'
    );

    IF acknowledgement <> 'acked' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'recovery probe terminal effect was not acknowledged',
            DETAIL = acknowledgement;
    END IF;

    RETURN QUERY
    SELECT
        state.probe_id,
        state.requested_event_id,
        state.completed_at
    FROM platform.probe_state_v0 state
    WHERE state.probe_id = new_probe_id;
END;
$$;

CREATE FUNCTION platform.backup_verification_state_v0()
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
        'failedMigrationRuns', (
            SELECT count(*)
            FROM platform.migration_runs
            WHERE status = 'failed'
        ),
        'probe', probe.probe
    )
    FROM LATERAL (
        SELECT jsonb_build_object(
            'probeId', state.probe_id,
            'eventId', state.requested_event_id,
            'probeStatus', state.status,
            'outboxState', event.state,
            'effectCount', count(effect.event_id),
            'completedAt', state.completed_at
        ) AS probe
        FROM platform.probe_state_v0 state
        JOIN platform.outbox_events event
          ON event.event_id = state.requested_event_id
        LEFT JOIN platform.probe_effects_v0 effect
          ON effect.event_id = state.requested_event_id
        WHERE state.status = 'completed'
        GROUP BY
            state.probe_id,
            state.requested_event_id,
            state.status,
            state.completed_at,
            event.state
        ORDER BY state.completed_at DESC
        LIMIT 1
    ) probe;
$$;

REVOKE ALL ON FUNCTION platform.create_recovery_probe_v0() FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.backup_verification_state_v0() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.create_recovery_probe_v0() TO liqi_backup;
GRANT EXECUTE ON FUNCTION platform.backup_verification_state_v0() TO liqi_backup, liqi_monitor;
