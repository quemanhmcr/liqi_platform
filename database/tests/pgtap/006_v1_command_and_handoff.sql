BEGIN;
SELECT plan(40);

SELECT has_schema('oban');
SELECT schema_owner_is('oban', 'liqi_owner');
SELECT ok(
    pg_catalog.has_function_privilege(
        'liqi_api',
        'platform.request_probe_v1(uuid,uuid,text,text,text,bigint,timestamptz,uuid,uuid,jsonb,timestamptz,jsonb)',
        'EXECUTE'
    ),
    'API role can execute the V1 command function'
);
SELECT ok(
    pg_catalog.has_function_privilege(
        'liqi_realtime',
        'platform.read_realtime_handoff_v1(bigint,integer)',
        'EXECUTE'
    ),
    'realtime role can execute the V1 handoff reader'
);
SELECT ok(
    pg_catalog.has_function_privilege(
        'liqi_worker',
        'platform.claim_outbox_v1(text,integer,integer)',
        'EXECUTE'
    ),
    'worker role can execute the V1 outbox claim function'
);
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_api', 'platform.command_idempotency_v1', 'SELECT'), 'API role cannot query idempotency authority directly');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_realtime', 'platform.realtime_handoff_events_v1', 'SELECT'), 'realtime role cannot query V1 handoff storage directly');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_api', 'oban.oban_jobs', 'SELECT'), 'API role cannot query Oban storage');
SELECT ok(
    has_table_privilege('liqi_worker', 'oban.oban_jobs', 'SELECT'),
    'worker role can read Oban jobs through the Oban adapter'
);
SELECT is(platform.current_migration_version_v0(), 8::bigint, 'platform migration version is 8');
SELECT is(platform.current_oban_migration_version_v1(), 14, 'Oban migration version is 14');
SELECT ok((SELECT ready FROM platform.database_readiness_v1(8, 14)), 'V1 database is migration ready');
SELECT ok((SELECT write_ready FROM platform.database_readiness_v1(8, 14)), 'primary database is command-write ready');
SELECT is((SELECT reason FROM platform.database_readiness_v1(8, 14)), 'ready', 'readiness reason is ready');

SET ROLE liqi_api;
SELECT lives_ok(
    $$SELECT * FROM platform.request_probe_v1(
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000101',
        'platform.probe.create.v1',
        'probe-command-001',
        '1111111111111111111111111111111111111111111111111111111111111111',
        0,
        '2026-07-18T02:00:00Z',
        '10000000-0000-4000-8000-000000000301',
        '10000000-0000-4000-8000-000000000401',
        '{"traceparent":"00-11111111111111111111111111111111-2222222222222222-01"}'::jsonb,
        '2026-07-18T02:00:05Z',
        '{"environment":"test"}'::jsonb
    )$$,
    'V1 command atomically writes idempotency, aggregate state, outbox, and handoff'
);
RESET ROLE;

SELECT is((SELECT count(*) FROM platform.probe_state_v0), 1::bigint, 'one aggregate state exists');
SELECT is((SELECT aggregate_version FROM platform.probe_state_v0), 1::bigint, 'aggregate version increments from zero to one');
SELECT is((SELECT count(*) FROM platform.outbox_events), 1::bigint, 'one shared outbox event exists');
SELECT is((SELECT protocol_version FROM platform.outbox_events), 1::smallint, 'outbox event uses protocol version one');
SELECT is((SELECT count(*) FROM platform.realtime_handoff_events_v1), 1::bigint, 'one committed V1 handoff row exists');
SELECT is((SELECT count(*) FROM platform.command_idempotency_v1), 1::bigint, 'one durable idempotency result exists');

SET ROLE liqi_api;
SELECT is(
    (SELECT duplicate FROM platform.request_probe_v1(
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000199',
        'platform.probe.create.v1',
        'probe-command-001',
        '1111111111111111111111111111111111111111111111111111111111111111',
        0
    )),
    true,
    'duplicate command returns the original durable outcome'
);
RESET ROLE;

SELECT is((SELECT count(*) FROM platform.outbox_events), 1::bigint, 'duplicate command creates no second outbox row');
SELECT is(
    (SELECT event_id FROM platform.command_idempotency_v1),
    '10000000-0000-4000-8000-000000000101'::uuid,
    'duplicate result retains the original event identity'
);

SET ROLE liqi_api;
SELECT throws_ok(
    $$SELECT * FROM platform.request_probe_v1(
        '10000000-0000-4000-8000-000000000002',
        '10000000-0000-4000-8000-000000000102',
        'platform.probe.create.v1',
        'probe-command-001',
        '2222222222222222222222222222222222222222222222222222222222222222',
        0
    )$$,
    'LQ001',
    NULL,
    'idempotency key reuse for a different command is rejected'
);
SELECT throws_ok(
    $$SELECT * FROM platform.request_probe_v1(
        '10000000-0000-4000-8000-000000000003',
        '10000000-0000-4000-8000-000000000103',
        'platform.probe.create.v1',
        'probe-command-003',
        '3333333333333333333333333333333333333333333333333333333333333333',
        1
    )$$,
    'LQ002',
    NULL,
    'stale aggregate version is rejected'
);
RESET ROLE;

SET ROLE liqi_realtime;
SELECT is((SELECT count(*) FROM platform.read_realtime_handoff_v1(0, 64)), 1::bigint, 'realtime reads one committed V1 handoff');
SELECT is(
    (SELECT payload_type FROM platform.read_realtime_handoff_v1(0, 64)),
    'platform.probe.requested.v1',
    'handoff preserves payload type'
);
SELECT throws_ok(
    $$SELECT * FROM platform.read_realtime_handoff_v1(-1, 64)$$,
    '22023',
    'after_handoff_id must be zero or greater',
    'negative V1 cursor is rejected'
);
RESET ROLE;

CREATE TEMP TABLE v1_claim AS
SELECT * FROM platform.claim_outbox_v1('pgtap-v1-worker', 1, 30);
GRANT SELECT ON v1_claim TO liqi_worker;
SELECT is((SELECT count(*) FROM v1_claim), 1::bigint, 'V1 worker claims one shared outbox event');
SELECT is((SELECT protocol_version FROM v1_claim), 1::smallint, 'claim exposes protocol version one');

SET ROLE liqi_worker;
SELECT is(
    platform.apply_probe_effect_and_ack_v1(
        (SELECT event_id FROM v1_claim),
        (SELECT claim_token FROM v1_claim),
        'pgtap-v1-worker'
    ),
    'acked',
    'V1 terminal effect and acknowledgement commit atomically'
);
SELECT is(
    platform.apply_probe_effect_and_ack_v1(
        (SELECT event_id FROM v1_claim),
        (SELECT claim_token FROM v1_claim),
        'pgtap-v1-worker'
    ),
    'already_succeeded',
    'repeated V1 terminal call is idempotent'
);
RESET ROLE;

SELECT ok((SELECT terminal FROM platform.observe_probe_v1(
    '10000000-0000-4000-8000-000000000001',
    '10000000-0000-4000-8000-000000000101'
)), 'V1 observation reports terminal success');
SELECT is(
    (SELECT count(*) FROM platform.observe_probe_v1(
        '10000000-0000-4000-8000-000000000099',
        '10000000-0000-4000-8000-000000000199'
    )),
    0::bigint,
    'unknown probe observation returns no row'
);
SET ROLE liqi_api;
SELECT throws_ok(
    $$SELECT * FROM platform.observe_probe_v1(
        '10000000-0000-4000-8000-000000000001',
        '10000000-0000-4000-8000-000000000199'
    )$$,
    'LQ003',
    NULL,
    'known probe with a different event id is rejected'
);
RESET ROLE;
SELECT is((SELECT state FROM platform.outbox_events), 'succeeded', 'shared outbox records terminal success');
SELECT is((SELECT count(*) FROM platform.probe_effects_v0), 1::bigint, 'terminal effect remains unique');

SAVEPOINT v1_atomic_command;
SET ROLE liqi_api;
SELECT lives_ok(
    $$SELECT * FROM platform.request_probe_v1(
        '10000000-0000-4000-8000-000000000004',
        '10000000-0000-4000-8000-000000000104',
        'platform.probe.create.v1',
        'probe-command-004',
        '4444444444444444444444444444444444444444444444444444444444444444',
        0
    )$$,
    'V1 command can execute inside a caller transaction'
);
RESET ROLE;
ROLLBACK TO SAVEPOINT v1_atomic_command;
SELECT is(
    (SELECT count(*) FROM platform.probe_state_v0 WHERE probe_id = '10000000-0000-4000-8000-000000000004'),
    0::bigint,
    'caller rollback removes state, idempotency, outbox, and handoff atomically'
);

SELECT * FROM finish();
ROLLBACK;
