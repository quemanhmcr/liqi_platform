CREATE TABLE platform.realtime_handoff_state_v1 (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    last_handoff_id bigint NOT NULL DEFAULT 0 CHECK (last_handoff_id >= 0),
    retained_after_handoff_id bigint NOT NULL DEFAULT 0 CHECK (retained_after_handoff_id >= 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (retained_after_handoff_id <= last_handoff_id)
);

INSERT INTO platform.realtime_handoff_state_v1 (
    singleton,
    last_handoff_id,
    retained_after_handoff_id
)
VALUES (true, 0, 0);

CREATE TABLE platform.realtime_handoff_events_v1 (
    handoff_id bigint PRIMARY KEY CHECK (handoff_id > 0),
    event_id uuid NOT NULL UNIQUE REFERENCES platform.outbox_events(event_id) ON DELETE RESTRICT,
    protocol_version smallint NOT NULL CHECK (protocol_version = 1),
    message_id uuid NOT NULL,
    correlation_id uuid,
    causation_id uuid,
    trace_context jsonb NOT NULL CHECK (
        jsonb_typeof(trace_context) = 'object'
        AND octet_length(trace_context::text) <= 2048
    ),
    deadline_at timestamptz,
    actor_key text NOT NULL CHECK (length(actor_key) BETWEEN 1 AND 160),
    priority text NOT NULL CHECK (priority IN ('durable', 'realtime', 'ephemeral', 'telemetry')),
    payload_type text NOT NULL CHECK (payload_type ~ '^[a-z][a-z0-9_.-]{2,127}$'),
    payload_version integer NOT NULL CHECK (payload_version >= 0),
    ordering_key text NOT NULL CHECK (length(ordering_key) BETWEEN 1 AND 128),
    occurred_at timestamptz NOT NULL,
    producer text NOT NULL CHECK (producer ~ '^liqi-[a-z0-9-]{2,48}$'),
    payload jsonb NOT NULL CHECK (
        jsonb_typeof(payload) = 'object'
        AND octet_length(payload::text) <= 65536
    ),
    metadata jsonb NOT NULL CHECK (
        jsonb_typeof(metadata) = 'object'
        AND octet_length(metadata::text) <= 4096
    ),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (message_id = event_id),
    CHECK (deadline_at IS NULL OR deadline_at >= occurred_at)
);

CREATE INDEX realtime_handoff_events_v1_recorded_idx
ON platform.realtime_handoff_events_v1 (recorded_at, handoff_id);

CREATE FUNCTION platform.publish_realtime_handoff_v1(requested_event_id uuid)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    existing_handoff_id bigint;
    next_handoff_id bigint;
BEGIN
    IF requested_event_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'event id is required';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended('liqi-realtime-handoff-v1:' || requested_event_id::text, 0)
    );

    SELECT handoff.handoff_id
    INTO existing_handoff_id
    FROM platform.realtime_handoff_events_v1 handoff
    WHERE handoff.event_id = requested_event_id;

    IF FOUND THEN
        RETURN existing_handoff_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM platform.outbox_events event
        WHERE event.event_id = requested_event_id
          AND event.protocol_version = 1
          AND COALESCE((event.metadata ->> 'realtime')::boolean, false)
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'V1 realtime handoff requires a protocol-v1 outbox event marked for realtime';
    END IF;

    UPDATE platform.realtime_handoff_state_v1 state
    SET last_handoff_id = state.last_handoff_id + 1,
        updated_at = clock_timestamp()
    WHERE state.singleton
    RETURNING state.last_handoff_id INTO next_handoff_id;

    INSERT INTO platform.realtime_handoff_events_v1 (
        handoff_id,
        event_id,
        protocol_version,
        message_id,
        correlation_id,
        causation_id,
        trace_context,
        deadline_at,
        actor_key,
        priority,
        payload_type,
        payload_version,
        ordering_key,
        occurred_at,
        producer,
        payload,
        metadata
    )
    SELECT
        next_handoff_id,
        event.event_id,
        event.protocol_version,
        event.message_id,
        event.correlation_id,
        event.causation_id,
        event.trace_context,
        event.deadline_at,
        event.actor_key,
        event.priority,
        event.payload_type,
        event.payload_version,
        event.ordering_key,
        event.occurred_at,
        event.producer,
        event.payload,
        event.metadata
    FROM platform.outbox_events event
    WHERE event.event_id = requested_event_id;

    RETURN next_handoff_id;
END;
$$;

CREATE FUNCTION platform.read_realtime_handoff_v1(
    after_handoff_id bigint DEFAULT 0,
    batch_size integer DEFAULT 64
)
RETURNS TABLE (
    handoff_id bigint,
    event_id uuid,
    protocol_version smallint,
    message_id uuid,
    correlation_id uuid,
    causation_id uuid,
    trace_context jsonb,
    deadline_at timestamptz,
    actor_key text,
    aggregate_key text,
    priority text,
    payload_type text,
    event_type text,
    payload_version integer,
    event_version integer,
    ordering_key text,
    occurred_at timestamptz,
    producer text,
    payload jsonb,
    metadata jsonb,
    recorded_at timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    retained_cursor bigint;
BEGIN
    IF after_handoff_id IS NULL OR after_handoff_id < 0 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'after_handoff_id must be zero or greater';
    END IF;
    IF batch_size IS NULL OR batch_size < 1 OR batch_size > 128 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'batch_size must be between 1 and 128';
    END IF;

    SELECT state.retained_after_handoff_id
    INTO retained_cursor
    FROM platform.realtime_handoff_state_v1 state
    WHERE state.singleton;

    IF after_handoff_id < retained_cursor THEN
        RAISE EXCEPTION USING
            ERRCODE = 'LQ004',
            MESSAGE = 'realtime cursor is older than the retained handoff watermark',
            DETAIL = json_build_object(
                'afterHandoffId', after_handoff_id,
                'retainedAfterHandoffId', retained_cursor
            )::text;
    END IF;

    RETURN QUERY
    SELECT
        handoff.handoff_id,
        handoff.event_id,
        handoff.protocol_version,
        handoff.message_id,
        handoff.correlation_id,
        handoff.causation_id,
        handoff.trace_context,
        handoff.deadline_at,
        handoff.actor_key,
        handoff.actor_key AS aggregate_key,
        handoff.priority,
        handoff.payload_type,
        handoff.payload_type AS event_type,
        handoff.payload_version,
        handoff.payload_version AS event_version,
        handoff.ordering_key,
        handoff.occurred_at,
        handoff.producer,
        handoff.payload,
        handoff.metadata,
        handoff.recorded_at
    FROM platform.realtime_handoff_events_v1 handoff
    WHERE handoff.handoff_id > after_handoff_id
    ORDER BY handoff.handoff_id
    LIMIT batch_size;
END;
$$;

CREATE FUNCTION platform.claim_outbox_v1(
    requested_consumer_id text,
    requested_batch_size integer DEFAULT 10,
    requested_lease_seconds integer DEFAULT 30
)
RETURNS TABLE (
    event_id uuid,
    claim_token uuid,
    attempt_no smallint,
    protocol_version smallint,
    message_id uuid,
    correlation_id uuid,
    causation_id uuid,
    trace_context jsonb,
    deadline_at timestamptz,
    actor_key text,
    aggregate_key text,
    priority text,
    payload_type text,
    event_type text,
    payload_version integer,
    event_version integer,
    ordering_key text,
    occurred_at timestamptz,
    producer text,
    payload jsonb,
    metadata jsonb,
    lease_expires_at timestamptz
)
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    WITH claimed AS (
        SELECT *
        FROM platform.claim_outbox_v0(
            requested_consumer_id,
            requested_batch_size,
            requested_lease_seconds
        )
    )
    SELECT
        claimed.event_id,
        claimed.claim_token,
        claimed.attempt_no,
        event.protocol_version,
        event.message_id,
        event.correlation_id,
        event.causation_id,
        event.trace_context,
        event.deadline_at,
        event.actor_key,
        event.actor_key AS aggregate_key,
        event.priority,
        event.payload_type,
        event.payload_type AS event_type,
        event.payload_version,
        event.payload_version AS event_version,
        event.ordering_key,
        event.occurred_at,
        event.producer,
        event.payload,
        event.metadata,
        claimed.lease_expires_at
    FROM claimed
    JOIN platform.outbox_events event USING (event_id)
    ORDER BY event.occurred_at, event.event_id;
$$;

CREATE FUNCTION platform.ack_outbox_v1(
    requested_event_id uuid,
    requested_claim_token uuid,
    requested_consumer_id text
)
RETURNS text
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT platform.ack_outbox_v0(
        requested_event_id,
        requested_claim_token,
        requested_consumer_id
    );
$$;

CREATE FUNCTION platform.fail_outbox_v1(
    requested_event_id uuid,
    requested_claim_token uuid,
    requested_consumer_id text,
    requested_error_code text,
    requested_retry_at timestamptz
)
RETURNS text
LANGUAGE sql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT platform.fail_outbox_v0(
        requested_event_id,
        requested_claim_token,
        requested_consumer_id,
        requested_error_code,
        requested_retry_at
    );
$$;

CREATE FUNCTION platform.request_probe_v1(
    requested_probe_id uuid,
    requested_event_id uuid,
    requested_scope text,
    requested_idempotency_key text,
    requested_request_fingerprint text,
    requested_expected_version bigint,
    requested_occurred_at timestamptz DEFAULT clock_timestamp(),
    requested_correlation_id uuid DEFAULT NULL,
    requested_causation_id uuid DEFAULT NULL,
    requested_trace_context jsonb DEFAULT '{}'::jsonb,
    requested_deadline_at timestamptz DEFAULT NULL,
    requested_metadata jsonb DEFAULT '{}'::jsonb
)
RETURNS TABLE (
    probe_id uuid,
    event_id uuid,
    aggregate_version bigint,
    handoff_cursor bigint,
    duplicate boolean,
    status text,
    outcome jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    fingerprint text;
    existing_record platform.command_idempotency_v1%ROWTYPE;
    current_version bigint;
    next_version bigint;
    created_handoff_cursor bigint;
    created_outcome jsonb;
BEGIN
    IF requested_scope IS NULL
       OR length(requested_scope) NOT BETWEEN 1 AND 96
       OR requested_scope !~ '^[a-z0-9][a-z0-9.:-]*$' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'idempotency scope has invalid format';
    END IF;
    IF requested_idempotency_key IS NULL
       OR length(requested_idempotency_key) NOT BETWEEN 1 AND 128 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'idempotency key must contain 1 to 128 characters';
    END IF;
    IF requested_expected_version IS NULL OR requested_expected_version < 0 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'expected aggregate version must be zero or greater';
    END IF;

    IF requested_request_fingerprint IS NULL
       OR requested_request_fingerprint !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION USING
            ERRCODE = '22023',
            MESSAGE = 'request fingerprint must be lowercase SHA-256 hex';
    END IF;

    fingerprint := requested_request_fingerprint;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            requested_scope || chr(31) || requested_idempotency_key,
            0
        )
    );

    SELECT *
    INTO existing_record
    FROM platform.command_idempotency_v1 record
    WHERE record.scope = requested_scope
      AND record.idempotency_key = requested_idempotency_key;

    IF FOUND THEN
        IF existing_record.request_fingerprint <> fingerprint THEN
            RAISE EXCEPTION USING
                ERRCODE = 'LQ001',
                MESSAGE = 'idempotency key was already used for a different command';
        END IF;

        RETURN QUERY
        SELECT
            (existing_record.outcome ->> 'probeId')::uuid,
            existing_record.event_id,
            existing_record.aggregate_version,
            (existing_record.outcome ->> 'handoffCursor')::bigint,
            true,
            'accepted'::text,
            existing_record.outcome;
        RETURN;
    END IF;

    SELECT state.aggregate_version
    INTO current_version
    FROM platform.probe_state_v0 state
    WHERE state.probe_id = requested_probe_id
    FOR UPDATE;

    current_version := COALESCE(current_version, 0);

    IF current_version <> requested_expected_version THEN
        RAISE EXCEPTION USING
            ERRCODE = 'LQ002',
            MESSAGE = 'stale aggregate version',
            DETAIL = json_build_object(
                'expectedVersion', requested_expected_version,
                'currentVersion', current_version
            )::text;
    END IF;

    IF current_version <> 0 THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'V1 platform probe supports only the create transition';
    END IF;

    next_version := current_version + 1;

    INSERT INTO platform.probe_state_v0 (
        probe_id,
        requested_event_id,
        status,
        requested_at,
        aggregate_version
    )
    VALUES (
        requested_probe_id,
        requested_event_id,
        'requested',
        requested_occurred_at,
        next_version
    );

    PERFORM platform.enqueue_outbox_v1(
        requested_event_id,
        'platform.probe.requested.v1',
        1,
        requested_occurred_at,
        'liqi-api',
        requested_correlation_id,
        requested_causation_id,
        'platform-probe:' || requested_probe_id::text,
        'platform-probe:' || requested_probe_id::text,
        COALESCE(requested_trace_context, '{}'::jsonb),
        requested_deadline_at,
        'durable',
        jsonb_build_object(
            'probeId', requested_probe_id,
            'aggregateVersion', next_version
        ),
        COALESCE(requested_metadata, '{}'::jsonb) || jsonb_build_object('realtime', true),
        8::smallint
    );

    created_handoff_cursor := platform.publish_realtime_handoff_v1(requested_event_id);
    created_outcome := jsonb_build_object(
        'probeId', requested_probe_id,
        'eventId', requested_event_id,
        'aggregateVersion', next_version,
        'handoffCursor', created_handoff_cursor
    );

    INSERT INTO platform.command_idempotency_v1 (
        scope,
        idempotency_key,
        request_fingerprint,
        aggregate_key,
        aggregate_version,
        event_id,
        outcome
    )
    VALUES (
        requested_scope,
        requested_idempotency_key,
        fingerprint,
        'platform-probe:' || requested_probe_id::text,
        next_version,
        requested_event_id,
        created_outcome
    );

    RETURN QUERY
    SELECT
        requested_probe_id,
        requested_event_id,
        next_version,
        created_handoff_cursor,
        false,
        'accepted'::text,
        created_outcome;
END;
$$;

CREATE FUNCTION platform.apply_probe_effect_and_ack_v1(
    requested_event_id uuid,
    requested_claim_token uuid,
    requested_consumer_id text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    event_record platform.outbox_events%ROWTYPE;
    target_probe_id uuid;
BEGIN
    SELECT *
    INTO event_record
    FROM platform.outbox_events event
    WHERE event.event_id = requested_event_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN 'not_found';
    END IF;
    IF event_record.state = 'succeeded' THEN
        RETURN 'already_succeeded';
    END IF;
    IF event_record.state = 'dead_letter' THEN
        RETURN 'already_dead_lettered';
    END IF;
    IF event_record.state <> 'processing'
       OR event_record.claim_token <> requested_claim_token
       OR event_record.claimed_by <> requested_consumer_id THEN
        RETURN 'stale_claim';
    END IF;
    IF event_record.protocol_version <> 1
       OR event_record.event_type <> 'platform.probe.requested.v1'
       OR event_record.event_version <> 1 THEN
        RETURN 'unsupported_event';
    END IF;

    target_probe_id := (event_record.payload ->> 'probeId')::uuid;

    INSERT INTO platform.probe_effects_v0 (event_id, probe_id)
    VALUES (event_record.event_id, target_probe_id)
    ON CONFLICT (event_id) DO NOTHING;

    UPDATE platform.probe_state_v0 state
    SET status = 'completed',
        completed_at = COALESCE(state.completed_at, clock_timestamp())
    WHERE state.probe_id = target_probe_id;

    RETURN platform.ack_outbox_v0(
        requested_event_id,
        requested_claim_token,
        requested_consumer_id
    );
END;
$$;

CREATE FUNCTION platform.observe_probe_v1(
    requested_probe_id uuid,
    requested_event_id uuid
)
RETURNS TABLE (
    probe_id uuid,
    event_id uuid,
    aggregate_version bigint,
    probe_status text,
    outbox_state text,
    effect_applied boolean,
    handoff_cursor bigint,
    terminal boolean,
    observed_at timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    authoritative_event_id uuid;
BEGIN
    SELECT state.requested_event_id
    INTO authoritative_event_id
    FROM platform.probe_state_v0 state
    WHERE state.probe_id = observe_probe_v1.requested_probe_id;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    IF authoritative_event_id <> observe_probe_v1.requested_event_id THEN
        RAISE EXCEPTION USING
            ERRCODE = 'LQ003',
            MESSAGE = 'probe identity does not match the requested event id';
    END IF;

    RETURN QUERY
    SELECT
        state.probe_id,
        event.event_id,
        state.aggregate_version,
        state.status,
        event.state,
        effect.applied_at IS NOT NULL,
        handoff.handoff_id,
        state.status = 'completed'
            AND event.state = 'succeeded'
            AND effect.applied_at IS NOT NULL,
        statement_timestamp()
    FROM platform.probe_state_v0 state
    JOIN platform.outbox_events event
      ON event.event_id = state.requested_event_id
    LEFT JOIN platform.probe_effects_v0 effect
      ON effect.event_id = event.event_id
    LEFT JOIN platform.realtime_handoff_events_v1 handoff
      ON handoff.event_id = event.event_id
    WHERE state.probe_id = observe_probe_v1.requested_probe_id
      AND event.event_id = observe_probe_v1.requested_event_id
      AND event.protocol_version = 1;
END;
$$;

CREATE FUNCTION platform.prune_realtime_handoff_v1(
    recorded_before timestamptz,
    requested_batch_size integer DEFAULT 500
)
RETURNS TABLE (
    deleted_count integer,
    retained_after_handoff_id bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    deleted_rows integer;
    highest_deleted bigint;
    resulting_watermark bigint;
BEGIN
    IF recorded_before IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'recorded_before is required';
    END IF;
    IF requested_batch_size IS NULL OR requested_batch_size < 1 OR requested_batch_size > 500 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'batch size must be between 1 and 500';
    END IF;

    WITH candidates AS (
        SELECT handoff.handoff_id
        FROM platform.realtime_handoff_events_v1 handoff
        WHERE handoff.recorded_at < recorded_before
        ORDER BY handoff.handoff_id
        LIMIT requested_batch_size
        FOR UPDATE SKIP LOCKED
    ), deleted AS (
        DELETE FROM platform.realtime_handoff_events_v1 handoff
        USING candidates
        WHERE handoff.handoff_id = candidates.handoff_id
        RETURNING handoff.handoff_id
    )
    SELECT count(*)::integer, max(handoff_id)
    INTO deleted_rows, highest_deleted
    FROM deleted;

    IF highest_deleted IS NOT NULL THEN
        UPDATE platform.realtime_handoff_state_v1 state
        SET retained_after_handoff_id = GREATEST(
                state.retained_after_handoff_id,
                highest_deleted
            ),
            updated_at = clock_timestamp()
        WHERE state.singleton;
    END IF;

    SELECT state.retained_after_handoff_id
    INTO resulting_watermark
    FROM platform.realtime_handoff_state_v1 state
    WHERE state.singleton;

    RETURN QUERY SELECT COALESCE(deleted_rows, 0), resulting_watermark;
END;
$$;

REVOKE ALL ON platform.realtime_handoff_state_v1, platform.realtime_handoff_events_v1 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform FROM PUBLIC;

GRANT EXECUTE ON FUNCTION platform.request_probe_v1(uuid, uuid, text, text, text, bigint, timestamptz, uuid, uuid, jsonb, timestamptz, jsonb)
    TO liqi_api;
GRANT EXECUTE ON FUNCTION platform.publish_realtime_handoff_v1(uuid)
    TO liqi_api;
GRANT EXECUTE ON FUNCTION platform.read_realtime_handoff_v1(bigint, integer)
    TO liqi_realtime;
GRANT EXECUTE ON FUNCTION platform.claim_outbox_v1(text, integer, integer)
    TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.ack_outbox_v1(uuid, uuid, text)
    TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.fail_outbox_v1(uuid, uuid, text, text, timestamptz)
    TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.apply_probe_effect_and_ack_v1(uuid, uuid, text)
    TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.observe_probe_v1(uuid, uuid)
    TO liqi_api, liqi_readonly, liqi_monitor, liqi_backup;
GRANT EXECUTE ON FUNCTION platform.prune_realtime_handoff_v1(timestamptz, integer)
    TO liqi_worker;

COMMENT ON TABLE platform.realtime_handoff_events_v1 IS
    'Read-only committed projection for protocol-v1 realtime delivery. PostgreSQL outbox and authority state remain authoritative.';
COMMENT ON FUNCTION platform.read_realtime_handoff_v1(bigint, integer) IS
    'Read committed V1 events strictly after a durable cursor. SQLSTATE LQ004 requires authority resynchronization after a retention gap.';
COMMENT ON FUNCTION platform.request_probe_v1(uuid, uuid, text, text, text, bigint, timestamptz, uuid, uuid, jsonb, timestamptz, jsonb) IS
    'Atomic V1 walking-skeleton command: idempotency, aggregate version, probe state, shared outbox, and committed realtime handoff.';
COMMENT ON FUNCTION platform.observe_probe_v1(uuid, uuid) IS
    'Least-privilege probe observation. Empty result means probe not found; SQLSTATE LQ003 means probe exists with a different authoritative event identity.';
