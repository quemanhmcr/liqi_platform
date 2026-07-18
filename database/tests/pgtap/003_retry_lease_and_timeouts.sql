BEGIN;
SELECT plan(19);

SELECT ok(
    EXISTS (
        SELECT 1 FROM pg_db_role_setting
        WHERE setrole = 'liqi_api'::regrole
          AND setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND 'statement_timeout=5s' = ANY(setconfig)
    ),
    'API statement timeout is applied'
);
SELECT ok(
    EXISTS (
        SELECT 1 FROM pg_db_role_setting
        WHERE setrole = 'liqi_worker'::regrole
          AND setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND 'lock_timeout=5s' = ANY(setconfig)
    ),
    'worker lock timeout is applied'
);
SELECT ok(
    EXISTS (
        SELECT 1 FROM pg_db_role_setting
        WHERE setrole = 'liqi_readonly'::regrole
          AND setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND 'default_transaction_read_only=on' = ANY(setconfig)
    ),
    'read-only role defaults to read-only transactions'
);

SELECT platform.request_probe_v0(
    '00000000-0000-4000-8000-000000000003',
    '00000000-0000-4000-8000-000000000103',
    clock_timestamp()
);

CREATE TEMP TABLE first_claim AS
SELECT * FROM platform.claim_outbox_v0('lease-worker-a', 1, 5);
SELECT is((SELECT count(*) FROM first_claim), 1::bigint, 'first claimant receives event');

UPDATE platform.outbox_events
SET lease_expires_at = clock_timestamp() - interval '1 second'
WHERE event_id = '00000000-0000-4000-8000-000000000103';

CREATE TEMP TABLE second_claim AS
SELECT * FROM platform.claim_outbox_v0('lease-worker-b', 1, 30);
SELECT is((SELECT count(*) FROM second_claim), 1::bigint, 'expired lease can be reclaimed');
SELECT is((SELECT attempt_no FROM second_claim), 2::smallint, 'reclaim increments attempt number');
SELECT isnt((SELECT claim_token FROM first_claim), (SELECT claim_token FROM second_claim), 'reclaim has a new claim token');
SELECT is(
    platform.ack_outbox_v0(
        '00000000-0000-4000-8000-000000000103',
        (SELECT claim_token FROM first_claim),
        'lease-worker-a'
    ),
    'stale_claim',
    'expired token cannot acknowledge newer lease'
);
SELECT is(
    platform.fail_outbox_v0(
        '00000000-0000-4000-8000-000000000103',
        (SELECT claim_token FROM second_claim),
        'lease-worker-b',
        'probe.retryable',
        clock_timestamp()
    ),
    'retry_scheduled',
    'failure schedules bounded retry'
);
SELECT is((SELECT state FROM platform.outbox_events WHERE event_id = '00000000-0000-4000-8000-000000000103'), 'pending', 'retry returns event to pending');
SELECT is((SELECT count(*) FROM platform.outbox_attempts WHERE event_id = '00000000-0000-4000-8000-000000000103'), 2::bigint, 'lease attempts are durable');
SELECT is((SELECT outcome FROM platform.outbox_attempts WHERE event_id = '00000000-0000-4000-8000-000000000103' AND attempt_no = 1), 'lease_expired', 'expired attempt is classified');
SELECT is((SELECT outcome FROM platform.outbox_attempts WHERE event_id = '00000000-0000-4000-8000-000000000103' AND attempt_no = 2), 'retry', 'retry attempt is classified');

UPDATE platform.outbox_events
SET max_attempts = attempt_count + 1, available_at = clock_timestamp()
WHERE event_id = '00000000-0000-4000-8000-000000000103';
CREATE TEMP TABLE final_claim AS
SELECT * FROM platform.claim_outbox_v0('lease-worker-c', 1, 30);
SELECT is((SELECT attempt_no FROM final_claim), 3::smallint, 'final bounded attempt is claimed');
SELECT is(
    platform.fail_outbox_v0(
        '00000000-0000-4000-8000-000000000103',
        (SELECT claim_token FROM final_claim),
        'lease-worker-c',
        'probe.exhausted',
        clock_timestamp()
    ),
    'dead_lettered',
    'attempt exhaustion transitions to dead letter'
);
SELECT is((SELECT state FROM platform.outbox_events WHERE event_id = '00000000-0000-4000-8000-000000000103'), 'dead_letter', 'dead letter is terminal');
SELECT is(
    platform.fail_outbox_v0(
        '00000000-0000-4000-8000-000000000103',
        (SELECT claim_token FROM final_claim),
        'lease-worker-c',
        'probe.exhausted',
        clock_timestamp()
    ),
    'already_dead_lettered',
    'repeated failure is idempotent after terminal state'
);

SAVEPOINT atomic_probe;
SELECT platform.request_probe_v0(
    '00000000-0000-4000-8000-000000000004',
    '00000000-0000-4000-8000-000000000104',
    clock_timestamp()
);
ROLLBACK TO SAVEPOINT atomic_probe;
SELECT is((SELECT count(*) FROM platform.probe_state_v0 WHERE probe_id = '00000000-0000-4000-8000-000000000004'), 0::bigint, 'probe state rolls back atomically');
SELECT is((SELECT count(*) FROM platform.outbox_events WHERE event_id = '00000000-0000-4000-8000-000000000104'), 0::bigint, 'outbox row rolls back atomically');

SELECT * FROM finish();
ROLLBACK;
