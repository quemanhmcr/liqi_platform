CREATE TABLE platform.realtime_handoff_counter_v0 (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    last_handoff_id bigint NOT NULL DEFAULT 0 CHECK (last_handoff_id >= 0)
);

INSERT INTO platform.realtime_handoff_counter_v0 (singleton, last_handoff_id)
VALUES (true, 0);

CREATE TABLE platform.realtime_handoff_events_v0 (
    handoff_id bigint PRIMARY KEY CHECK (handoff_id > 0),
    event_id uuid NOT NULL UNIQUE REFERENCES platform.outbox_events(event_id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_.-]{2,127}$'),
    event_version integer NOT NULL CHECK (event_version >= 0),
    occurred_at timestamptz NOT NULL,
    aggregate_key text NOT NULL CHECK (length(aggregate_key) BETWEEN 1 AND 256),
    ordering_key text NOT NULL CHECK (length(ordering_key) BETWEEN 1 AND 256),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

COMMENT ON TABLE platform.realtime_handoff_events_v0 IS
    'Read-only, at-least-once handoff projection for events selected for realtime delivery. PostgreSQL outbox remains durable authority.';
COMMENT ON COLUMN platform.realtime_handoff_events_v0.handoff_id IS
    'Gap-free commit-ordered cursor allocated under a transactional singleton row lock.';

CREATE FUNCTION platform.publish_realtime_handoff_v0(requested_event_id uuid)
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

    -- Serialize duplicate publication for the same immutable event without exposing
    -- advisory-lock semantics to runtime consumers.
    PERFORM pg_advisory_xact_lock(hashtextextended('liqi-realtime-handoff:' || requested_event_id::text, 0));

    SELECT handoff.handoff_id
    INTO existing_handoff_id
    FROM platform.realtime_handoff_events_v0 handoff
    WHERE handoff.event_id = requested_event_id;

    IF FOUND THEN
        RETURN existing_handoff_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM platform.outbox_events event
        WHERE event.event_id = requested_event_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23503',
            MESSAGE = 'realtime handoff requires an existing durable outbox event';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM platform.outbox_events event
        WHERE event.event_id = requested_event_id
          AND event.event_type = 'platform.probe.requested.v0'
          AND event.event_version = 0
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '0A000',
            MESSAGE = 'V0 realtime handoff only supports platform.probe.requested.v0 version 0';
    END IF;

    -- This row lock is intentionally held until the producer transaction commits.
    -- A later publisher cannot obtain a larger handoff ID and commit ahead of an
    -- earlier publisher, so polling by handoff_id cannot skip a late commit.
    UPDATE platform.realtime_handoff_counter_v0 counter
    SET last_handoff_id = counter.last_handoff_id + 1
    WHERE counter.singleton
    RETURNING counter.last_handoff_id INTO next_handoff_id;

    INSERT INTO platform.realtime_handoff_events_v0 (
        handoff_id,
        event_id,
        event_type,
        event_version,
        occurred_at,
        aggregate_key,
        ordering_key,
        payload
    )
    SELECT
        next_handoff_id,
        event.event_id,
        event.event_type,
        event.event_version,
        event.occurred_at,
        event.aggregate_key,
        event.ordering_key,
        event.payload
    FROM platform.outbox_events event
    WHERE event.event_id = requested_event_id;

    RETURN next_handoff_id;
END;
$$;

CREATE FUNCTION platform.read_realtime_handoff_v0(
    after_handoff_id bigint DEFAULT 0,
    batch_size integer DEFAULT 64
)
RETURNS TABLE (
    handoff_id bigint,
    event_id uuid,
    event_type text,
    event_version integer,
    occurred_at timestamptz,
    aggregate_key text,
    ordering_key text,
    payload jsonb,
    recorded_at timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
BEGIN
    IF after_handoff_id IS NULL OR after_handoff_id < 0 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'after_handoff_id must be zero or greater';
    END IF;
    IF batch_size IS NULL OR batch_size < 1 OR batch_size > 128 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'batch_size must be between 1 and 128';
    END IF;

    RETURN QUERY
    SELECT
        handoff.handoff_id,
        handoff.event_id,
        handoff.event_type,
        handoff.event_version,
        handoff.occurred_at,
        handoff.aggregate_key,
        handoff.ordering_key,
        handoff.payload,
        handoff.recorded_at
    FROM platform.realtime_handoff_events_v0 handoff
    WHERE handoff.handoff_id > after_handoff_id
    ORDER BY handoff.handoff_id
    LIMIT batch_size;
END;
$$;

CREATE FUNCTION platform.observe_probe_v0(
    requested_probe_id uuid,
    requested_event_id uuid
)
RETURNS TABLE (
    probe_id uuid,
    event_id uuid,
    probe_status text,
    outbox_state text,
    effect_applied boolean,
    probe_completed_at timestamptz,
    effect_applied_at timestamptz,
    terminal boolean,
    observed_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT
        state.probe_id,
        event.event_id,
        state.status,
        event.state,
        effect.applied_at IS NOT NULL,
        state.completed_at,
        effect.applied_at,
        state.status = 'completed'
            AND event.state = 'succeeded'
            AND effect.applied_at IS NOT NULL,
        statement_timestamp()
    FROM platform.probe_state_v0 state
    JOIN platform.outbox_events event
      ON event.event_id = state.requested_event_id
    LEFT JOIN platform.probe_effects_v0 effect
      ON effect.event_id = event.event_id
    WHERE state.probe_id = requested_probe_id
      AND event.event_id = requested_event_id;
$$;

CREATE OR REPLACE FUNCTION platform.request_probe_v0(
    probe_id uuid,
    event_id uuid,
    occurred_at timestamptz DEFAULT clock_timestamp()
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
BEGIN
    INSERT INTO platform.probe_state_v0 (probe_id, requested_event_id, requested_at)
    VALUES (probe_id, event_id, occurred_at)
    ON CONFLICT (probe_id) DO NOTHING;

    IF NOT EXISTS (
        SELECT 1
        FROM platform.probe_state_v0 state
        WHERE state.probe_id = request_probe_v0.probe_id
          AND state.requested_event_id = request_probe_v0.event_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = 'probe id already exists with a different event id';
    END IF;

    PERFORM platform.enqueue_outbox_v0(
        event_id,
        'platform.probe.requested.v0',
        0,
        occurred_at,
        'platform-probe:' || probe_id::text,
        'platform-probe:' || probe_id::text,
        jsonb_build_object('probeId', probe_id::text),
        8
    );

    PERFORM platform.publish_realtime_handoff_v0(event_id);
    RETURN event_id;
END;
$$;

REVOKE ALL ON platform.realtime_handoff_counter_v0, platform.realtime_handoff_events_v0 FROM PUBLIC;
REVOKE SELECT ON platform.realtime_handoff_counter_v0, platform.realtime_handoff_events_v0
    FROM liqi_api, liqi_realtime, liqi_worker, liqi_readonly;
REVOKE ALL ON FUNCTION platform.publish_realtime_handoff_v0(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.read_realtime_handoff_v0(bigint, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.observe_probe_v0(uuid, uuid) FROM PUBLIC;

-- Promotion observation is function-only. The temporary direct-table query may be
-- removed by Senior 3 after this migration is integrated.
REVOKE SELECT ON platform.probe_state_v0, platform.probe_effects_v0 FROM liqi_readonly;

GRANT EXECUTE ON FUNCTION platform.publish_realtime_handoff_v0(uuid) TO liqi_api;
GRANT EXECUTE ON FUNCTION platform.read_realtime_handoff_v0(bigint, integer) TO liqi_realtime;
GRANT EXECUTE ON FUNCTION platform.observe_probe_v0(uuid, uuid) TO liqi_readonly;

COMMENT ON FUNCTION platform.publish_realtime_handoff_v0(uuid) IS
    'Publish an existing durable outbox event into the committed realtime handoff within the producer transaction.';
COMMENT ON FUNCTION platform.read_realtime_handoff_v0(bigint, integer) IS
    'Read committed realtime handoff rows strictly after the supplied durable cursor; no claim or acknowledgement semantics.';
COMMENT ON FUNCTION platform.observe_probe_v0(uuid, uuid) IS
    'Least-privilege promotion/disposable-test observation of probe, outbox and idempotent terminal-effect state.';
