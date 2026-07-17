BEGIN;
SELECT plan(27);

SET ROLE liqi_api;
SELECT lives_ok(
    $$SELECT platform.request_probe_v0(
        '00000000-0000-4000-8000-000000000005',
        '00000000-0000-4000-8000-000000000105',
        '2026-07-17T01:00:00Z',
        '00000000-0000-4000-8000-000000000305',
        '00000000-0000-4000-8000-000000000405',
        '{"environment":"test"}'::jsonb
    )$$,
    'API producer writes probe, outbox and realtime handoff atomically'
);
SELECT lives_ok(
    $$SELECT platform.publish_realtime_handoff_v0('00000000-0000-4000-8000-000000000105')$$,
    'repeated realtime publication is accepted idempotently by event ID'
);
RESET ROLE;

SELECT is((SELECT count(*) FROM platform.realtime_handoff_events_v0), 1::bigint, 'one durable handoff row exists');
SELECT is((SELECT count(*) FROM platform.outbox_events), 1::bigint, 'handoff references one durable outbox event');

SET ROLE liqi_realtime;
SELECT is((SELECT count(*) FROM platform.read_realtime_handoff_v0(0, 64)), 1::bigint, 'realtime reads one committed handoff through provider function');
SELECT is((SELECT event_id FROM platform.read_realtime_handoff_v0(0, 64)), '00000000-0000-4000-8000-000000000105'::uuid, 'handoff preserves event ID');
SELECT is((SELECT event_type FROM platform.read_realtime_handoff_v0(0, 64)), 'platform.probe.requested.v0', 'handoff preserves event type');
SELECT is((SELECT event_version FROM platform.read_realtime_handoff_v0(0, 64)), 0, 'handoff preserves event version');
SELECT is((SELECT schema_version FROM platform.read_realtime_handoff_v0(0, 64)), 0::smallint, 'handoff preserves schema version');
SELECT is((SELECT producer FROM platform.read_realtime_handoff_v0(0, 64)), 'liqi-api', 'handoff preserves producer');
SELECT is((SELECT correlation_id FROM platform.read_realtime_handoff_v0(0, 64)), '00000000-0000-4000-8000-000000000305'::uuid, 'handoff preserves correlation ID');
SELECT is((SELECT causation_id FROM platform.read_realtime_handoff_v0(0, 64)), '00000000-0000-4000-8000-000000000405'::uuid, 'handoff preserves causation ID');
SELECT is((SELECT metadata ->> 'environment' FROM platform.read_realtime_handoff_v0(0, 64)), 'test', 'handoff preserves metadata');
SELECT is((SELECT aggregate_key FROM platform.read_realtime_handoff_v0(0, 64)), 'platform-probe:00000000-0000-4000-8000-000000000005', 'handoff preserves aggregate key');
SELECT is((SELECT ordering_key FROM platform.read_realtime_handoff_v0(0, 64)), 'platform-probe:00000000-0000-4000-8000-000000000005', 'handoff preserves ordering key');
SELECT is((SELECT payload ->> 'probeId' FROM platform.read_realtime_handoff_v0(0, 64)), '00000000-0000-4000-8000-000000000005', 'handoff preserves payload');
SELECT is(
    (SELECT count(*) FROM platform.read_realtime_handoff_v0(
        (SELECT handoff_id FROM platform.read_realtime_handoff_v0(0, 64)), 64
    )),
    0::bigint,
    'resume cursor is strictly greater than the last delivered handoff ID'
);
SELECT throws_ok(
    $$SELECT * FROM platform.read_realtime_handoff_v0(-1, 64)$$,
    '22023',
    'after_handoff_id must be zero or greater',
    'negative cursor is rejected'
);
SELECT throws_ok(
    $$SELECT * FROM platform.read_realtime_handoff_v0(0, 129)$$,
    '22023',
    'batch_size must be between 1 and 128',
    'unbounded batch is rejected'
);
RESET ROLE;

SET ROLE liqi_worker;
CREATE TEMP TABLE runtime_handoff_claim AS
SELECT * FROM platform.claim_outbox_v0('pgtap-runtime-handoff-worker', 1, 30);
SELECT is((SELECT count(*) FROM runtime_handoff_claim), 1::bigint, 'worker independently claims the durable outbox event');
SELECT is(
    platform.apply_probe_effect_and_ack_v0(
        (SELECT event_id FROM runtime_handoff_claim),
        (SELECT claim_token FROM runtime_handoff_claim),
        'pgtap-runtime-handoff-worker'
    ),
    'acked',
    'worker applies terminal effect and acknowledges outbox'
);
RESET ROLE;

SET ROLE liqi_readonly;
SELECT is((SELECT probe_status FROM platform.observe_probe_v0(
    '00000000-0000-4000-8000-000000000005',
    '00000000-0000-4000-8000-000000000105'
)), 'completed', 'probe observer reports completed state');
SELECT is((SELECT outbox_state FROM platform.observe_probe_v0(
    '00000000-0000-4000-8000-000000000005',
    '00000000-0000-4000-8000-000000000105'
)), 'succeeded', 'probe observer reports succeeded outbox state');
SELECT ok((SELECT effect_applied FROM platform.observe_probe_v0(
    '00000000-0000-4000-8000-000000000005',
    '00000000-0000-4000-8000-000000000105'
)), 'probe observer confirms idempotent effect');
SELECT ok((SELECT terminal FROM platform.observe_probe_v0(
    '00000000-0000-4000-8000-000000000005',
    '00000000-0000-4000-8000-000000000105'
)), 'probe observer computes terminal success');
SELECT is((SELECT count(*) FROM platform.observe_probe_v0(
    '00000000-0000-4000-8000-000000000005',
    '00000000-0000-4000-8000-000000000999'
)), 0::bigint, 'mismatched probe/event pair returns no row');
RESET ROLE;

SET ROLE liqi_api;
SELECT platform.enqueue_outbox_v0(
    '00000000-0000-4000-8000-000000000205',
    0,
    'platform.unsupported.v0',
    0,
    clock_timestamp(),
    'liqi-api',
    NULL,
    NULL,
    'platform-unsupported',
    'platform-unsupported',
    '{}'::jsonb,
    '{}'::jsonb,
    8
);
SELECT throws_ok(
    $$SELECT platform.publish_realtime_handoff_v0('00000000-0000-4000-8000-000000000205')$$,
    '0A000',
    'V0 realtime handoff only supports platform.probe.requested.v0 version 0',
    'V0 rejects business or unspecified realtime event publication'
);
RESET ROLE;

SELECT * FROM finish();
ROLLBACK;
