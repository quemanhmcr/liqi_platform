BEGIN;
SELECT plan(12);

SELECT has_function_privilege('liqi_backup', 'platform.create_recovery_probe_v0()', 'EXECUTE');
SELECT has_function_privilege('liqi_backup', 'platform.backup_verification_state_v0()', 'EXECUTE');
SELECT hasnt_function_privilege('liqi_api', 'platform.create_recovery_probe_v0()', 'EXECUTE');

SET ROLE liqi_backup;
CREATE TEMP TABLE recovery_probe AS
SELECT * FROM platform.create_recovery_probe_v0();
SELECT is((SELECT count(*) FROM recovery_probe), 1::bigint, 'recovery probe returns one identity');
SELECT is((SELECT status FROM platform.probe_state_v0 WHERE probe_id = (SELECT probe_id FROM recovery_probe)), 'completed', 'recovery probe state is completed');
SELECT is((SELECT state FROM platform.outbox_events WHERE event_id = (SELECT event_id FROM recovery_probe)), 'succeeded', 'recovery probe outbox is terminal');
SELECT is((SELECT count(*) FROM platform.probe_effects_v0 WHERE event_id = (SELECT event_id FROM recovery_probe)), 1::bigint, 'recovery probe terminal effect is unique');
SELECT is((SELECT count(*) FROM platform.outbox_attempts WHERE event_id = (SELECT event_id FROM recovery_probe)), 1::bigint, 'recovery probe records one durable attempt');
SELECT is((SELECT outcome FROM platform.outbox_attempts WHERE event_id = (SELECT event_id FROM recovery_probe)), 'succeeded', 'recovery probe attempt succeeds');
SELECT is((platform.backup_verification_state_v0() ->> 'migrationVersion')::bigint, 3::bigint, 'backup state reports current migration');
SELECT is(platform.backup_verification_state_v0() #>> '{probe,probeStatus}', 'completed', 'backup state reports terminal probe');
SELECT is((platform.backup_verification_state_v0() #>> '{probe,effectCount}')::integer, 1, 'backup state reports one terminal effect');
RESET ROLE;

SELECT * FROM finish();
ROLLBACK;
