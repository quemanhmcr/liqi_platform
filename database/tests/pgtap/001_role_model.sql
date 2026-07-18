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

SELECT isnt_superuser('liqi_migrator');
SELECT isnt_superuser('liqi_api');
SELECT isnt_superuser('liqi_realtime');
SELECT isnt_superuser('liqi_worker');
SELECT isnt_superuser('liqi_readonly');
SELECT isnt_superuser('liqi_monitor');
SELECT isnt_superuser('liqi_backup');

SELECT has_schema('platform');
SELECT schema_owner_is('platform', 'liqi_owner');
SELECT ok(NOT pg_catalog.has_schema_privilege('liqi_api', 'platform', 'CREATE'), 'API role cannot create in platform schema');
SELECT ok(NOT pg_catalog.has_schema_privilege('liqi_worker', 'platform', 'CREATE'), 'worker role cannot create in platform schema');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_readonly', 'platform.outbox_events', 'INSERT'), 'readonly role cannot insert outbox events');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_backup', 'platform.outbox_events', 'SELECT'), 'backup role cannot query outbox authority directly');
SELECT ok(pg_catalog.has_function_privilege('liqi_worker', 'platform.claim_outbox_v0(text,integer,integer)', 'EXECUTE'), 'worker role can execute the V0 claim function');

SELECT is((SELECT rolconnlimit FROM pg_roles WHERE rolname = 'liqi_api'), 20, 'API role connection cap is 20');
SELECT is((SELECT rolconnlimit FROM pg_roles WHERE rolname = 'liqi_realtime'), 5, 'realtime role connection cap is 5');
SELECT is((SELECT rolconnlimit FROM pg_roles WHERE rolname = 'liqi_worker'), 10, 'worker role connection cap is 10');
SELECT ok(pg_catalog.has_function_privilege('liqi_realtime', 'platform.read_realtime_handoff_v0(bigint,integer)', 'EXECUTE'), 'realtime role can execute the V0 handoff reader');
SELECT ok(NOT pg_catalog.has_function_privilege('liqi_realtime', 'platform.claim_outbox_v0(text,integer,integer)', 'EXECUTE'), 'realtime role cannot claim outbox events');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_realtime', 'platform.realtime_handoff_events_v0', 'SELECT'), 'realtime role cannot query V0 handoff storage directly');
SELECT ok(pg_catalog.has_function_privilege('liqi_readonly', 'platform.observe_probe_v0(uuid,uuid)', 'EXECUTE'), 'readonly role can execute bounded V0 probe observation');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_readonly', 'platform.probe_state_v0', 'SELECT'), 'readonly role cannot query probe authority directly');
SELECT ok(NOT pg_catalog.has_table_privilege('liqi_readonly', 'platform.outbox_events', 'SELECT'), 'readonly role cannot query outbox authority directly');

SELECT * FROM finish();
ROLLBACK;
