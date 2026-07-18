ALTER TABLE platform.outbox_events
    ADD COLUMN protocol_version smallint NOT NULL DEFAULT 0,
    ADD COLUMN message_id uuid GENERATED ALWAYS AS (event_id) STORED,
    ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN deadline_at timestamptz,
    ADD COLUMN actor_key text GENERATED ALWAYS AS (aggregate_key) STORED,
    ADD COLUMN priority text NOT NULL DEFAULT 'durable',
    ADD COLUMN payload_type text GENERATED ALWAYS AS (event_type) STORED,
    ADD COLUMN payload_version integer GENERATED ALWAYS AS (event_version) STORED;

ALTER TABLE platform.outbox_events
    ADD CONSTRAINT outbox_events_protocol_version_v1_check
        CHECK (protocol_version BETWEEN 0 AND 1),
    ADD CONSTRAINT outbox_events_trace_context_v1_check
        CHECK (jsonb_typeof(trace_context) = 'object' AND octet_length(trace_context::text) <= 2048),
    ADD CONSTRAINT outbox_events_deadline_v1_check
        CHECK (deadline_at IS NULL OR deadline_at >= occurred_at),
    ADD CONSTRAINT outbox_events_priority_v1_check
        CHECK (priority IN ('durable', 'realtime', 'ephemeral', 'telemetry')),
    ADD CONSTRAINT outbox_events_payload_size_v1_check
        CHECK (octet_length(payload::text) <= 65536);

ALTER TABLE platform.probe_state_v0
    ADD COLUMN aggregate_version bigint NOT NULL DEFAULT 1
        CHECK (aggregate_version > 0);

-- Preserve the published V0 signature and durable behavior while removing a
-- PostgreSQL 17 PL/pgSQL ambiguity between named parameters and column names.
CREATE OR REPLACE FUNCTION platform.request_probe_v0(
    probe_id uuid,
    event_id uuid,
    occurred_at timestamptz DEFAULT clock_timestamp(),
    correlation_id uuid DEFAULT NULL,
    causation_id uuid DEFAULT NULL,
    metadata jsonb DEFAULT '{}'::jsonb
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
BEGIN
    INSERT INTO platform.probe_state_v0 AS state (
        probe_id,
        requested_event_id,
        requested_at
    )
    VALUES (
        request_probe_v0.probe_id,
        request_probe_v0.event_id,
        request_probe_v0.occurred_at
    )
    ON CONFLICT ON CONSTRAINT probe_state_v0_pkey DO NOTHING;

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
        request_probe_v0.event_id,
        0::smallint,
        'platform.probe.requested.v0',
        0,
        request_probe_v0.occurred_at,
        'liqi-api',
        request_probe_v0.correlation_id,
        request_probe_v0.causation_id,
        'platform-probe:' || request_probe_v0.probe_id::text,
        'platform-probe:' || request_probe_v0.probe_id::text,
        jsonb_build_object('probeId', request_probe_v0.probe_id::text),
        COALESCE(request_probe_v0.metadata, '{}'::jsonb),
        8::smallint
    );

    PERFORM platform.publish_realtime_handoff_v0(request_probe_v0.event_id);
    RETURN request_probe_v0.event_id;
END;
$$;

COMMENT ON FUNCTION platform.request_probe_v0(uuid, uuid, timestamptz, uuid, uuid, jsonb) IS
    'V0-compatible atomic probe command with PostgreSQL 17-safe parameter qualification; retained for the rollback window.';

CREATE OR REPLACE FUNCTION platform.apply_probe_effect_and_ack_v0(
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
    SELECT event.*
    INTO event_record
    FROM platform.outbox_events event
    WHERE event.event_id = apply_probe_effect_and_ack_v0.event_id
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
    IF event_record.event_type <> 'platform.probe.requested.v0'
       OR event_record.event_version <> 0 THEN
        RETURN 'unsupported_event';
    END IF;

    target_probe_id := (event_record.payload ->> 'probeId')::uuid;

    INSERT INTO platform.probe_effects_v0 (event_id, probe_id)
    VALUES (event_record.event_id, target_probe_id)
    ON CONFLICT ON CONSTRAINT probe_effects_v0_pkey DO NOTHING;

    UPDATE platform.probe_state_v0 state
    SET status = 'completed',
        completed_at = COALESCE(state.completed_at, clock_timestamp())
    WHERE state.probe_id = target_probe_id;

    RETURN platform.ack_outbox_v0(
        apply_probe_effect_and_ack_v0.event_id,
        apply_probe_effect_and_ack_v0.claim_token,
        apply_probe_effect_and_ack_v0.consumer_id
    );
END;
$$;

COMMENT ON FUNCTION platform.apply_probe_effect_and_ack_v0(uuid, uuid, text) IS
    'V0-compatible idempotent probe effect and outbox acknowledgement with PostgreSQL 17-safe conflict targeting.';

CREATE OR REPLACE FUNCTION platform.observe_probe_v0(
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
    WHERE state.probe_id = $1
      AND event.event_id = $2;
$$;

COMMENT ON FUNCTION platform.observe_probe_v0(uuid, uuid) IS
    'V0-compatible bounded observation with positional input binding to avoid output-column shadowing.';

CREATE TABLE platform.command_idempotency_v1 (
    scope text NOT NULL CHECK (
        length(scope) BETWEEN 1 AND 96
        AND scope ~ '^[a-z0-9][a-z0-9.:-]*$'
    ),
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    aggregate_key text NOT NULL CHECK (length(aggregate_key) BETWEEN 1 AND 160),
    aggregate_version bigint NOT NULL CHECK (aggregate_version > 0),
    event_id uuid NOT NULL UNIQUE REFERENCES platform.outbox_events(event_id) ON DELETE RESTRICT,
    outcome jsonb NOT NULL CHECK (
        jsonb_typeof(outcome) = 'object'
        AND octet_length(outcome::text) <= 16384
    ),
    completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '30 days'),
    PRIMARY KEY (scope, idempotency_key),
    CHECK (expires_at > completed_at)
);

CREATE INDEX command_idempotency_v1_expires_idx
ON platform.command_idempotency_v1 (expires_at, scope, idempotency_key);

CREATE FUNCTION platform.enqueue_outbox_v1(
    requested_event_id uuid,
    requested_event_type text,
    requested_event_version integer,
    requested_occurred_at timestamptz,
    requested_producer text,
    requested_correlation_id uuid,
    requested_causation_id uuid,
    requested_aggregate_key text,
    requested_ordering_key text,
    requested_trace_context jsonb,
    requested_deadline_at timestamptz,
    requested_priority text,
    requested_payload jsonb,
    requested_metadata jsonb DEFAULT '{}'::jsonb,
    requested_max_attempts smallint DEFAULT 8
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
BEGIN
    INSERT INTO platform.outbox_events (
        event_id,
        schema_version,
        protocol_version,
        event_type,
        event_version,
        occurred_at,
        producer,
        correlation_id,
        causation_id,
        aggregate_key,
        ordering_key,
        trace_context,
        deadline_at,
        priority,
        payload,
        metadata,
        max_attempts
    )
    VALUES (
        requested_event_id,
        0,
        1,
        requested_event_type,
        requested_event_version,
        requested_occurred_at,
        requested_producer,
        requested_correlation_id,
        requested_causation_id,
        requested_aggregate_key,
        requested_ordering_key,
        COALESCE(requested_trace_context, '{}'::jsonb),
        requested_deadline_at,
        requested_priority,
        requested_payload,
        COALESCE(requested_metadata, '{}'::jsonb),
        requested_max_attempts
    );

    RETURN requested_event_id;
EXCEPTION
    WHEN unique_violation THEN
        IF EXISTS (
            SELECT 1
            FROM platform.outbox_events existing
            WHERE existing.event_id = requested_event_id
              AND existing.schema_version = 0
              AND existing.protocol_version = 1
              AND existing.event_type = requested_event_type
              AND existing.event_version = requested_event_version
              AND existing.occurred_at = requested_occurred_at
              AND existing.producer = requested_producer
              AND existing.correlation_id IS NOT DISTINCT FROM requested_correlation_id
              AND existing.causation_id IS NOT DISTINCT FROM requested_causation_id
              AND existing.aggregate_key = requested_aggregate_key
              AND existing.ordering_key = requested_ordering_key
              AND existing.trace_context = COALESCE(requested_trace_context, '{}'::jsonb)
              AND existing.deadline_at IS NOT DISTINCT FROM requested_deadline_at
              AND existing.priority = requested_priority
              AND existing.payload = requested_payload
              AND existing.metadata = COALESCE(requested_metadata, '{}'::jsonb)
              AND existing.max_attempts = requested_max_attempts
        ) THEN
            RETURN requested_event_id;
        END IF;

        RAISE EXCEPTION USING
            ERRCODE = '23505',
            MESSAGE = 'outbox event id already exists with different durable content';
END;
$$;

CREATE FUNCTION platform.read_idempotency_v1(
    requested_scope text,
    requested_idempotency_key text
)
RETURNS TABLE (
    scope text,
    idempotency_key text,
    aggregate_key text,
    aggregate_version bigint,
    event_id uuid,
    outcome jsonb,
    completed_at timestamptz,
    expires_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, platform
AS $$
    SELECT
        record.scope,
        record.idempotency_key,
        record.aggregate_key,
        record.aggregate_version,
        record.event_id,
        record.outcome,
        record.completed_at,
        record.expires_at
    FROM platform.command_idempotency_v1 record
    WHERE record.scope = requested_scope
      AND record.idempotency_key = requested_idempotency_key;
$$;

REVOKE ALL ON platform.command_idempotency_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.enqueue_outbox_v1(uuid, text, integer, timestamptz, text, uuid, uuid, text, text, jsonb, timestamptz, text, jsonb, jsonb, smallint) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform.read_idempotency_v1(text, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION platform.enqueue_outbox_v1(uuid, text, integer, timestamptz, text, uuid, uuid, text, text, jsonb, timestamptz, text, jsonb, jsonb, smallint)
    TO liqi_api, liqi_worker;
GRANT EXECUTE ON FUNCTION platform.read_idempotency_v1(text, text)
    TO liqi_api, liqi_readonly;

COMMENT ON TABLE platform.command_idempotency_v1 IS
    'Durable command idempotency authority. Transaction advisory locking is applied by command functions before lookup or insert.';
COMMENT ON FUNCTION platform.enqueue_outbox_v1(uuid, text, integer, timestamptz, text, uuid, uuid, text, text, jsonb, timestamptz, text, jsonb, jsonb, smallint) IS
    'Insert a protocol-v1 event into the shared durable outbox. Duplicate event IDs are accepted only for byte-equivalent durable content.';
