BEGIN;
SELECT plan(31);

SELECT has_role('liqi_owner');
SELECT has_role('liqi_migrator');
SELECT has_role('liqi_api');
SELECT has_role('liqi_realtime');
SELECT has_role('liqi_worker');
SELECT has_role('liqi_readonly');
SELECT has_role('liqi_monitor');
SELECT has_role('liqi_backup');

SELECT isnt_super('liqi_migrator');
SELECT isnt_super('liqi_api');
SELECT isnt_super('liqi_realtime');
SELECT isnt_super('liqi_worker');
SELECT isnt_super('liqi_readonly');
SELECT isnt_super('liqi_monitor');
SELECT isnt_super('liqi_backup');

SELECT has_schema('platform');
SELECT schema_owner_is('platform', 'liqi_owner');
SELECT hasnt_schema_privilege('liqi_api', 'platform', 'CREATE');
SELECT hasnt_schema_privilege('liqi_worker', 'platform', 'CREATE');
SELECT hasnt_table_privilege('liqi_readonly', 'platform.outbox_events', 'INSERT');
SELECT hasnt_table_privilege('liqi_backup', 'platform.outbox_events', 'SELECT');
SELECT has_function_privilege('liqi_worker', 'platform.claim_outbox_v0(text,integer,integer)', 'EXECUTE');

SELECT is((SELECT rolconnlimit FROM pg_roles WHERE rolname = 'liqi_api'), 20, 'API role connection cap is 20');
SELECT is((SELECT rolconnlimit FROM pg_roles WHERE rolname = 'liqi_realtime'), 5, 'realtime role connection cap is 5');
SELECT is((SELECT rolconnlimit FROM pg_roles WHERE rolname = 'liqi_worker'), 10, 'worker role connection cap is 10');
SELECT has_function_privilege('liqi_realtime', 'platform.read_realtime_handoff_v0(bigint,integer)', 'EXECUTE');
SELECT hasnt_function_privilege('liqi_realtime', 'platform.claim_outbox_v0(text,integer,integer)', 'EXECUTE');
SELECT hasnt_table_privilege('liqi_realtime', 'platform.realtime_handoff_events_v0', 'SELECT');
SELECT has_function_privilege('liqi_readonly', 'platform.observe_probe_v0(uuid,uuid)', 'EXECUTE');
SELECT hasnt_table_privilege('liqi_readonly', 'platform.probe_state_v0', 'SELECT');
SELECT hasnt_table_privilege('liqi_readonly', 'platform.outbox_events', 'SELECT');

SELECT * FROM finish();
ROLLBACK;
