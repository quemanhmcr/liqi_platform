BEGIN;
SELECT plan(17);

SET ROLE liqi_api;
SELECT lives_ok(
    $$SELECT platform.request_probe_v0('00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000101', '2026-07-17T00:00:00Z')$$,
    'producer can atomically request the platform probe'
);
RESET ROLE;

SELECT is((SELECT count(*) FROM platform.probe_state_v0), 1::bigint, 'probe state inserted');
SELECT is((SELECT count(*) FROM platform.outbox_events), 1::bigint, 'outbox row inserted');
SELECT is((SELECT state FROM platform.outbox_events), 'pending', 'event starts pending');
SELECT is((SELECT attempt_count FROM platform.outbox_events), 0::smallint, 'attempt count starts at zero');

SET ROLE liqi_worker;
CREATE TEMP TABLE claimed AS
SELECT * FROM platform.claim_outbox_v0('pgtap-worker', 1, 30);
SELECT is((SELECT count(*) FROM claimed), 1::bigint, 'worker claims one event');
SELECT is((SELECT attempt_no FROM claimed), 1::smallint, 'first lease is attempt one');
SELECT is(
    platform.apply_probe_effect_and_ack_v0(
        (SELECT event_id FROM claimed),
        (SELECT claim_token FROM claimed),
        'pgtap-worker'
    ),
    'acked',
    'probe effect and ack succeed atomically'
);
SELECT is(
    platform.apply_probe_effect_and_ack_v0(
        (SELECT event_id FROM claimed),
        (SELECT claim_token FROM claimed),
        'pgtap-worker'
    ),
    'already_succeeded',
    'repeated terminal call is idempotent'
);
RESET ROLE;

SELECT is((SELECT count(*) FROM platform.probe_effects_v0), 1::bigint, 'terminal effect is unique');
SELECT is((SELECT status FROM platform.probe_state_v0), 'completed', 'probe is completed');
SELECT is((SELECT state FROM platform.outbox_events), 'succeeded', 'outbox is succeeded');
SELECT is((SELECT count(*) FROM platform.outbox_attempts), 1::bigint, 'one attempt recorded');
SELECT is((SELECT outcome FROM platform.outbox_attempts), 'succeeded', 'attempt outcome recorded');

SET ROLE liqi_api;
SELECT throws_ok(
    $$CREATE TABLE platform.forbidden_ddl(id integer)$$,
    '42501',
    NULL,
    'runtime role cannot create tables'
);
RESET ROLE;

SELECT is(current_setting('transaction_isolation'), 'read committed', 'default isolation is read committed');
SELECT is((SELECT max_attempts FROM platform.outbox_events), 8::smallint, 'retry count is bounded');

SELECT * FROM finish();
ROLLBACK;
