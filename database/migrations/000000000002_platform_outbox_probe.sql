CREATE TABLE platform.outbox_events (
    event_id uuid PRIMARY KEY,
    schema_version smallint NOT NULL DEFAULT 0 CHECK (schema_version = 0),
    event_type text NOT NULL CHECK (event_type ~ '^[a-z][a-z0-9_.-]{2,127}$'),
    event_version integer NOT NULL CHECK (event_version >= 0),
    occurred_at timestamptz NOT NULL,
    aggregate_key text NOT NULL CHECK (length(aggregate_key) BETWEEN 1 AND 256),
    ordering_key text NOT NULL CHECK (length(ordering_key) BETWEEN 1 AND 256),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'processing', 'succeeded', 'dead_letter')),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    attempt_count smallint NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts smallint NOT NULL DEFAULT 8 CHECK (max_attempts BETWEEN 1 AND 20),
    claim_token uuid,
    claimed_by text CHECK (claimed_by IS NULL OR length(claimed_by) BETWEEN 1 AND 128),
    lease_expires_at timestamptz,
    completed_at timestamptz,
    dead_lettered_at timestamptz,
    last_error_code text CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z0-9_.-]{1,128}$'),
    last_error_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (state = 'pending' AND claim_token IS NULL AND claimed_by IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL AND dead_lettered_at IS NULL)
        OR (state = 'processing' AND claim_token IS NOT NULL AND claimed_by IS NOT NULL AND lease_expires_at IS NOT NULL AND completed_at IS NULL AND dead_lettered_at IS NULL)
        OR (state = 'succeeded' AND claim_token IS NULL AND claimed_by IS NULL AND lease_expires_at IS NULL AND completed_at IS NOT NULL AND dead_lettered_at IS NULL)
        OR (state = 'dead_letter' AND claim_token IS NULL AND claimed_by IS NULL AND lease_expires_at IS NULL AND completed_at IS NULL AND dead_lettered_at IS NOT NULL)
    )
);

CREATE INDEX outbox_events_claim_pending_idx
ON platform.outbox_events (available_at, occurred_at, event_id)
WHERE state = 'pending';

CREATE INDEX outbox_events_claim_expired_idx
ON platform.outbox_events (lease_expires_at, occurred_at, event_id)
WHERE state = 'processing';

CREATE INDEX outbox_events_ordering_idx
ON platform.outbox_events (ordering_key, occurred_at, event_id);

CREATE TABLE platform.outbox_attempts (
    event_id uuid NOT NULL REFERENCES platform.outbox_events(event_id) ON DELETE RESTRICT,
    attempt_no smallint NOT NULL CHECK (attempt_no > 0),
    claim_token uuid NOT NULL UNIQUE,
    consumer_id text NOT NULL CHECK (length(consumer_id) BETWEEN 1 AND 128),
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at timestamptz NOT NULL,
    finished_at timestamptz,
    outcome text CHECK (outcome IS NULL OR outcome IN ('succeeded', 'retry', 'dead_letter', 'lease_expired')),
    error_code text CHECK (error_code IS NULL OR error_code ~ '^[a-z0-9_.-]{1,128}$'),
    PRIMARY KEY (event_id, attempt_no),
    CHECK ((finished_at IS NULL AND outcome IS NULL) OR (finished_at IS NOT NULL AND outcome IS NOT NULL))
);

CREATE TABLE platform.probe_state_v0 (
    probe_id uuid PRIMARY KEY,
    requested_event_id uuid NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'completed')),
    requested_at timestamptz NOT NULL,
    completed_at timestamptz,
    CHECK ((status = 'requested' AND completed_at IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL))
);

CREATE TABLE platform.probe_effects_v0 (
    event_id uuid PRIMARY KEY REFERENCES platform.outbox_events(event_id) ON DELETE RESTRICT,
    probe_id uuid NOT NULL UNIQUE REFERENCES platform.probe_state_v0(probe_id) ON DELETE RESTRICT,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION platform.enqueue_outbox_v0(
    event_id uuid,
    event_type text,
    event_version integer,
    occurred_at timestamptz,
    aggregate_key text,
    ordering_key text,
    payload jsonb,
    max_attempts smallint DEFAULT 8
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
BEGIN
    INSERT INTO platform.outbox_events (
        event_id, event_type, event_version, occurred_at,
        aggregate_key, ordering_key, payload, max_attempts
    )
    VALUES (
        event_id, event_type, event_version, occurred_at,
        aggregate_key, ordering_key, payload, max_attempts
    );
    RETURN event_id;
EXCEPTION
    WHEN unique_violation THEN
        IF EXISTS (
            SELECT 1
            FROM platform.outbox_events existing
            WHERE existing.event_id = enqueue_outbox_v0.event_id
              AND existing.event_type = enqueue_outbox_v0.event_type
              AND existing.event_version = enqueue_outbox_v0.event_version
              AND existing.occurred_at = enqueue_outbox_v0.occurred_at
              AND existing.aggregate_key = enqueue_outbox_v0.aggregate_key
              AND existing.ordering_key = enqueue_outbox_v0.ordering_key
              AND existing.payload = enqueue_outbox_v0.payload
        ) THEN
            RETURN event_id;
        END IF;
        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = 'outbox event id already exists with different durable content';
END;
$$;

CREATE FUNCTION platform.request_probe_v0(
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

    RETURN event_id;
END;
$$;

CREATE FUNCTION platform.claim_outbox_v0(
    consumer_id text,
    batch_size integer DEFAULT 10,
    lease_seconds integer DEFAULT 30
)
RETURNS TABLE (
    event_id uuid,
    claim_token uuid,
    attempt_no smallint,
    event_type text,
    event_version integer,
    occurred_at timestamptz,
    aggregate_key text,
    ordering_key text,
    payload jsonb,
    lease_expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
BEGIN
    IF consumer_id IS NULL OR length(consumer_id) NOT BETWEEN 1 AND 128 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'consumer id must contain 1 to 128 characters';
    END IF;
    IF batch_size NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'batch size must be between 1 and 50';
    END IF;
    IF lease_seconds NOT BETWEEN 5 AND 300 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'lease must be between 5 and 300 seconds';
    END IF;

    UPDATE platform.outbox_attempts attempt
    SET finished_at = clock_timestamp(), outcome = 'lease_expired'
    FROM platform.outbox_events event
    WHERE event.event_id = attempt.event_id
      AND event.state = 'processing'
      AND event.lease_expires_at <= clock_timestamp()
      AND attempt.claim_token = event.claim_token
      AND attempt.finished_at IS NULL;

    UPDATE platform.outbox_events event
    SET state = 'dead_letter',
        claim_token = NULL,
        claimed_by = NULL,
        lease_expires_at = NULL,
        dead_lettered_at = clock_timestamp(),
        last_error_code = COALESCE(last_error_code, 'lease.exhausted'),
        last_error_at = clock_timestamp(),
        updated_at = clock_timestamp()
    WHERE event.state IN ('pending', 'processing')
      AND (event.state = 'pending' OR event.lease_expires_at <= clock_timestamp())
      AND event.attempt_count >= event.max_attempts;

    RETURN QUERY
    WITH candidates AS (
        SELECT candidate.event_id
        FROM platform.outbox_events candidate
        WHERE (
            (candidate.state = 'pending' AND candidate.available_at <= clock_timestamp())
            OR (candidate.state = 'processing' AND candidate.lease_expires_at <= clock_timestamp())
        )
          AND candidate.attempt_count < candidate.max_attempts
        ORDER BY candidate.available_at, candidate.occurred_at, candidate.event_id
        FOR UPDATE SKIP LOCKED
        LIMIT batch_size
    ), claimed AS (
        UPDATE platform.outbox_events event
        SET state = 'processing',
            attempt_count = event.attempt_count + 1,
            claim_token = gen_random_uuid(),
            claimed_by = consumer_id,
            lease_expires_at = clock_timestamp() + make_interval(secs => lease_seconds),
            updated_at = clock_timestamp()
        FROM candidates
        WHERE event.event_id = candidates.event_id
        RETURNING event.*
    ), attempts AS (
        INSERT INTO platform.outbox_attempts (
            event_id, attempt_no, claim_token, consumer_id, claimed_at, lease_expires_at
        )
        SELECT
            claimed.event_id,
            claimed.attempt_count,
            claimed.claim_token,
            claimed.claimed_by,
            clock_timestamp(),
            claimed.lease_expires_at
        FROM claimed
        RETURNING outbox_attempts.event_id
    )
    SELECT
        claimed.event_id,
        claimed.claim_token,
        claimed.attempt_count,
        claimed.event_type,
        claimed.event_version,
        claimed.occurred_at,
        claimed.aggregate_key,
        claimed.ordering_key,
        claimed.payload,
        claimed.lease_expires_at
    FROM claimed
    JOIN attempts USING (event_id)
    ORDER BY claimed.occurred_at, claimed.event_id;
END;
$$;

CREATE FUNCTION platform.ack_outbox_v0(
    event_id uuid,
    claim_token uuid,
    consumer_id text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    current_state text;
BEGIN
    SELECT state INTO current_state
    FROM platform.outbox_events
    WHERE outbox_events.event_id = ack_outbox_v0.event_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN 'not_found';
    END IF;
    IF current_state = 'succeeded' THEN
        RETURN 'already_succeeded';
    END IF;
    IF current_state = 'dead_letter' THEN
        RETURN 'already_dead_lettered';
    END IF;

    UPDATE platform.outbox_events
    SET state = 'succeeded',
        claim_token = NULL,
        claimed_by = NULL,
        lease_expires_at = NULL,
        completed_at = clock_timestamp(),
        updated_at = clock_timestamp()
    WHERE outbox_events.event_id = ack_outbox_v0.event_id
      AND state = 'processing'
      AND outbox_events.claim_token = ack_outbox_v0.claim_token
      AND claimed_by = consumer_id;

    IF NOT FOUND THEN
        RETURN 'stale_claim';
    END IF;

    UPDATE platform.outbox_attempts
    SET finished_at = clock_timestamp(), outcome = 'succeeded'
    WHERE outbox_attempts.claim_token = ack_outbox_v0.claim_token
      AND outbox_attempts.consumer_id = ack_outbox_v0.consumer_id
      AND finished_at IS NULL;

    RETURN 'acked';
END;
$$;

CREATE FUNCTION platform.fail_outbox_v0(
    event_id uuid,
    claim_token uuid,
    consumer_id text,
    error_code text,
    retry_at timestamptz
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
DECLARE
    event_record platform.outbox_events%ROWTYPE;
    result_status text;
BEGIN
    IF error_code IS NULL OR error_code !~ '^[a-z0-9_.-]{1,128}$' THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'error code has invalid format';
    END IF;

    SELECT * INTO event_record
    FROM platform.outbox_events
    WHERE outbox_events.event_id = fail_outbox_v0.event_id
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
       OR event_record.claim_token <> fail_outbox_v0.claim_token
       OR event_record.claimed_by <> fail_outbox_v0.consumer_id THEN
        RETURN 'stale_claim';
    END IF;

    IF event_record.attempt_count >= event_record.max_attempts THEN
        UPDATE platform.outbox_events
        SET state = 'dead_letter',
            claim_token = NULL,
            claimed_by = NULL,
            lease_expires_at = NULL,
            dead_lettered_at = clock_timestamp(),
            last_error_code = fail_outbox_v0.error_code,
            last_error_at = clock_timestamp(),
            updated_at = clock_timestamp()
        WHERE outbox_events.event_id = fail_outbox_v0.event_id;
        result_status := 'dead_lettered';
    ELSE
        UPDATE platform.outbox_events
        SET state = 'pending',
            claim_token = NULL,
            claimed_by = NULL,
            lease_expires_at = NULL,
            available_at = GREATEST(retry_at, clock_timestamp()),
            last_error_code = fail_outbox_v0.error_code,
            last_error_at = clock_timestamp(),
            updated_at = clock_timestamp()
        WHERE outbox_events.event_id = fail_outbox_v0.event_id;
        result_status := 'retry_scheduled';
    END IF;

    UPDATE platform.outbox_attempts
    SET finished_at = clock_timestamp(),
        outcome = CASE WHEN result_status = 'dead_lettered' THEN 'dead_letter' ELSE 'retry' END,
        error_code = fail_outbox_v0.error_code
    WHERE outbox_attempts.claim_token = fail_outbox_v0.claim_token
      AND outbox_attempts.consumer_id = fail_outbox_v0.consumer_id
      AND finished_at IS NULL;

    RETURN result_status;
END;
$$;

CREATE FUNCTION platform.apply_probe_effect_and_ack_v0(
    event_id uuid,
    claim_token uuid,
    consumer_id text
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
    SELECT * INTO event_record
    FROM platform.outbox_events
    WHERE outbox_events.event_id = apply_probe_effect_and_ack_v0.event_id
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
       OR event_record.claim_token <> apply_probe_effect_and_ack_v0.claim_token
       OR event_record.claimed_by <> apply_probe_effect_and_ack_v0.consumer_id THEN
        RETURN 'stale_claim';
    END IF;
    IF event_record.event_type <> 'platform.probe.requested.v0' OR event_record.event_version <> 0 THEN
        RETURN 'unsupported_event';
    END IF;

    target_probe_id := (event_record.payload ->> 'probeId')::uuid;

    INSERT INTO platform.probe_effects_v0 (event_id, probe_id)
    VALUES (event_record.event_id, target_probe_id)
    ON CONFLICT (event_id) DO NOTHING;

    UPDATE platform.probe_state_v0
    SET status = 'completed', completed_at = COALESCE(completed_at, clock_timestamp())
    WHERE probe_state_v0.probe_id = target_probe_id;

    RETURN platform.ack_outbox_v0(event_id, claim_token, consumer_id);
END;
$$;

CREATE VIEW platform.outbox_health_v0
WITH (security_barrier = true)
AS
SELECT
    count(*) FILTER (WHERE state = 'pending') AS pending_count,
    count(*) FILTER (WHERE state = 'processing') AS processing_count,
    count(*) FILTER (WHERE state = 'succeeded') AS succeeded_count,
    count(*) FILTER (WHERE state = 'dead_letter') AS dead_letter_count,
    COALESCE(
        EXTRACT(EPOCH FROM (clock_timestamp() - min(created_at) FILTER (WHERE state = 'pending'))),
        0
    )::double precision AS oldest_pending_seconds,
    clock_timestamp() AS observed_at
FROM platform.outbox_events;

REVOKE ALL ON platform.outbox_events, platform.outbox_attempts, platform.probe_state_v0, platform.probe_effects_v0 FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform FROM PUBLIC;

GRANT EXECUTE ON FUNCTION platform.enqueue_outbox_v0(uuid, text, integer, timestamptz, text, text, jsonb, smallint) TO liqi_api, liqi_worker;
GRANT EXECUTE ON FUNCTION platform.request_probe_v0(uuid, uuid, timestamptz) TO liqi_api;
GRANT EXECUTE ON FUNCTION platform.claim_outbox_v0(text, integer, integer) TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.ack_outbox_v0(uuid, uuid, text) TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.fail_outbox_v0(uuid, uuid, text, text, timestamptz) TO liqi_worker;
GRANT EXECUTE ON FUNCTION platform.apply_probe_effect_and_ack_v0(uuid, uuid, text) TO liqi_worker;
GRANT SELECT ON platform.outbox_health_v0 TO liqi_monitor, liqi_backup;
GRANT SELECT ON platform.probe_state_v0, platform.probe_effects_v0 TO liqi_readonly, liqi_monitor, liqi_backup;
