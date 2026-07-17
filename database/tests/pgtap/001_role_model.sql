BEGIN;
SELECT plan(22);

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

SELECT * FROM finish();
ROLLBACK;
