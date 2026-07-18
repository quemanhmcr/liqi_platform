BEGIN;
SELECT plan(28);

SELECT is(
    pg_catalog.obj_description('oban.oban_jobs'::regclass, 'pg_class'),
    '14',
    'Oban table comment records migration version 14'
);
SELECT is(
    (SELECT relpersistence::text FROM pg_class WHERE oid = 'oban.oban_jobs'::regclass),
    'p',
    'Oban jobs are logged durable work state'
);
SELECT is(
    (SELECT relpersistence::text FROM pg_class WHERE oid = 'oban.oban_peers'::regclass),
    'u',
    'Oban peer coordination is unlogged and rebuildable'
);
SELECT ok(pg_catalog.has_table_privilege('liqi_worker', 'oban.oban_jobs', 'INSERT'), 'worker can insert Oban jobs');
SELECT ok(pg_catalog.has_table_privilege('liqi_worker', 'oban.oban_jobs', 'UPDATE'), 'worker can update Oban jobs');
SELECT ok(pg_catalog.has_table_privilege('liqi_worker', 'oban.oban_jobs', 'DELETE'), 'worker can delete Oban jobs');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_api', 'oban.oban_jobs', 'SELECT'), 'API role cannot query Oban storage');

SET ROLE liqi_worker;
SELECT lives_ok(
    $$INSERT INTO oban.oban_jobs (queue, worker, args, max_attempts, priority)
      VALUES ('maintenance', 'LiqiJobs.MaintenanceWorker', '{"operation":"prune"}', 5, 0)$$,
    'worker can persist a bounded Oban maintenance job'
);
RESET ROLE;
SELECT is((SELECT count(*) FROM oban.oban_jobs), 1::bigint, 'one Oban job is durable');

SET ROLE liqi_worker;
SELECT throws_ok(
    $$INSERT INTO oban.oban_jobs (queue, worker, args, max_attempts)
      VALUES ('maintenance', 'LiqiJobs.InvalidWorker', '{}', 0)$$,
    '23514',
    NULL,
    'database rejects an unbounded invalid retry policy'
);
RESET ROLE;
SELECT is((SELECT available_count FROM platform.oban_health_v1), 1::bigint, 'Oban health exposes available depth');

SET ROLE liqi_api;
SELECT lives_ok(
    $$SELECT * FROM platform.request_probe_v1(
        '20000000-0000-4000-8000-000000000001',
        '20000000-0000-4000-8000-000000000101',
        'platform.probe.create.v1',
        'retention-probe-001',
        '5555555555555555555555555555555555555555555555555555555555555555',
        0,
        clock_timestamp() - interval '8 days'
    )$$,
    'retention fixture uses the real V1 command transaction'
);
RESET ROLE;

UPDATE platform.realtime_handoff_events_v1
SET recorded_at = clock_timestamp() - interval '8 days'
WHERE event_id = '20000000-0000-4000-8000-000000000101';

SET ROLE liqi_worker;
SELECT is(
    (SELECT deleted_count FROM platform.prune_realtime_handoff_v1(clock_timestamp() - interval '7 days', 500)),
    1,
    'realtime pruning deletes one bounded expired projection row'
);
RESET ROLE;
SELECT is(
    (SELECT retained_after_handoff_id FROM platform.realtime_handoff_state_v1 WHERE singleton),
    1::bigint,
    'durable handoff watermark advances to the highest pruned cursor'
);

SET ROLE liqi_realtime;
SELECT throws_ok(
    $$SELECT * FROM platform.read_realtime_handoff_v1(0, 64)$$,
    'LQ004',
    NULL,
    'cursor older than retention watermark requires authority resynchronization'
);
SELECT is(
    (SELECT count(*) FROM platform.read_realtime_handoff_v1(1, 64)),
    0::bigint,
    'consumer may resume at the durable retained watermark after resynchronization'
);
RESET ROLE;

UPDATE platform.command_idempotency_v1
SET completed_at = clock_timestamp() - interval '40 days',
    expires_at = clock_timestamp() - interval '31 days'
WHERE scope = 'platform.probe.create.v1'
  AND idempotency_key = 'retention-probe-001';

SET ROLE liqi_worker;
SELECT is(
    platform.prune_command_idempotency_v1(clock_timestamp() - interval '30 days', 500),
    1,
    'expired idempotency records are pruned in bounded batches'
);
RESET ROLE;
SELECT is((SELECT count(*) FROM platform.command_idempotency_v1), 0::bigint, 'idempotency retention removed the expired record');

SET ROLE liqi_worker;
SELECT is(
    platform.prune_outbox_v1(
        clock_timestamp() + interval '1 day',
        clock_timestamp() + interval '1 day',
        500
    ),
    0,
    'outbox pruning preserves rows referenced by durable aggregate/probe state'
);
RESET ROLE;
SELECT is((SELECT count(*) FROM platform.outbox_events), 1::bigint, 'referenced outbox authority remains durable');
SELECT is((SELECT oban_migration_version FROM platform.database_readiness_v1(8, 14)), 14, 'readiness includes Oban migration version');
SELECT is((platform.backup_verification_state_v1() ->> 'migrationVersion')::bigint, 8::bigint, 'backup verification includes migration 8');
SELECT is((platform.backup_verification_state_v1() ->> 'obanMigrationVersion')::integer, 14, 'backup verification includes Oban migration 14');
SELECT is((SELECT pending_count FROM platform.outbox_health_v1), 1::bigint, 'outbox health exposes pending depth');
SELECT is((SELECT retained_count FROM platform.realtime_handoff_health_v1), 0::bigint, 'handoff health exposes retained depth after pruning');

SET ROLE liqi_worker;
SELECT throws_ok(
    $$SELECT * FROM platform.prune_realtime_handoff_v1(clock_timestamp(), 501)$$,
    '22023',
    'batch size must be between 1 and 500',
    'realtime prune batch is bounded'
);
SELECT throws_ok(
    $$SELECT platform.prune_command_idempotency_v1(clock_timestamp(), 501)$$,
    '22023',
    'batch size must be between 1 and 500',
    'idempotency prune batch is bounded'
);
SELECT throws_ok(
    $$SELECT platform.prune_outbox_v1(clock_timestamp(), clock_timestamp(), 501)$$,
    '22023',
    'batch size must be between 1 and 500',
    'outbox prune batch is bounded'
);
RESET ROLE;

SELECT * FROM finish();
ROLLBACK;
