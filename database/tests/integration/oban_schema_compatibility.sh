#!/usr/bin/env bash
set -euo pipefail
PSQL=${PSQL:-psql}
if [[ "$PSQL" == */* ]]; then
  [[ -f "$PSQL" ]] || { echo "required command missing: $PSQL" >&2; exit 69; }
else
  command -v "$PSQL" >/dev/null 2>&1 || { echo "required command missing: $PSQL" >&2; exit 69; }
fi

"$PSQL" --no-psqlrc --quiet --set=ON_ERROR_STOP=1 <<'SQL'
DO $compatibility$
DECLARE
    observed_columns text[];
    observed_constraints text[];
    observed_indexes text[];
    observed_states text[];
BEGIN
    SELECT array_agg(attribute.attname ORDER BY attribute.attnum)
    INTO observed_columns
    FROM pg_catalog.pg_attribute attribute
    WHERE attribute.attrelid = 'oban.oban_jobs'::regclass
      AND attribute.attnum > 0
      AND NOT attribute.attisdropped;

    IF observed_columns IS DISTINCT FROM ARRAY[
        'id','state','queue','worker','args','errors','attempt','max_attempts',
        'inserted_at','scheduled_at','attempted_at','completed_at','attempted_by',
        'discarded_at','priority','tags','meta','cancelled_at'
    ]::text[] THEN
        RAISE EXCEPTION 'Oban 2.23 job columns diverge: %', observed_columns;
    END IF;

    SELECT array_agg(constraint_record.conname || ':' || constraint_record.convalidated::text ORDER BY constraint_record.conname)
    INTO observed_constraints
    FROM pg_catalog.pg_constraint constraint_record
    WHERE constraint_record.conrelid = 'oban.oban_jobs'::regclass;

    IF observed_constraints IS DISTINCT FROM ARRAY[
        'attempt_range:true',
        'non_negative_priority:false',
        'oban_jobs_pkey:true',
        'positive_max_attempts:true',
        'queue_length:true',
        'worker_length:true'
    ]::text[] THEN
        RAISE EXCEPTION 'Oban 2.23 constraints diverge: %', observed_constraints;
    END IF;

    SELECT array_agg(index_record.indexname ORDER BY index_record.indexname)
    INTO observed_indexes
    FROM pg_catalog.pg_indexes index_record
    WHERE index_record.schemaname = 'oban'
      AND index_record.tablename = 'oban_jobs';

    IF observed_indexes IS DISTINCT FROM ARRAY[
        'oban_jobs_args_index',
        'oban_jobs_meta_index',
        'oban_jobs_pkey',
        'oban_jobs_state_cancelled_at_index',
        'oban_jobs_state_discarded_at_index',
        'oban_jobs_state_queue_priority_scheduled_at_id_index'
    ]::text[] THEN
        RAISE EXCEPTION 'Oban 2.23 indexes diverge: %', observed_indexes;
    END IF;

    SELECT array_agg(enum_value.enumlabel ORDER BY enum_value.enumsortorder)
    INTO observed_states
    FROM pg_catalog.pg_enum enum_value
    WHERE enum_value.enumtypid = 'oban.oban_job_state'::regtype;

    IF observed_states IS DISTINCT FROM ARRAY[
        'available','suspended','scheduled','executing','retryable','completed','discarded','cancelled'
    ]::text[] THEN
        RAISE EXCEPTION 'Oban 2.23 state enum diverges: %', observed_states;
    END IF;

    IF pg_catalog.obj_description('oban.oban_jobs'::regclass, 'pg_class') IS DISTINCT FROM '14' THEN
        RAISE EXCEPTION 'Oban migration comment is not 14';
    END IF;

    IF (SELECT relpersistence FROM pg_catalog.pg_class WHERE oid = 'oban.oban_jobs'::regclass) <> 'p'
       OR (SELECT relpersistence FROM pg_catalog.pg_class WHERE oid = 'oban.oban_peers'::regclass) <> 'u' THEN
        RAISE EXCEPTION 'Oban logged/unlogged persistence contract diverges';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_trigger trigger_record
        WHERE trigger_record.tgrelid = 'oban.oban_jobs'::regclass
          AND NOT trigger_record.tgisinternal
    ) OR to_regprocedure('oban.oban_jobs_notify()') IS NOT NULL THEN
        RAISE EXCEPTION 'Oban v14 must not retain the removed PostgreSQL notifier trigger/function';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_attribute attribute
        WHERE attribute.attrelid IN ('oban.oban_jobs'::regclass, 'oban.oban_peers'::regclass)
          AND attribute.attname IN ('inserted_at','scheduled_at','attempted_at','completed_at','discarded_at','cancelled_at','started_at','expires_at')
          AND attribute.atttypmod <> -1
    ) THEN
        RAISE EXCEPTION 'Oban timestamp typmods diverge from official migration output';
    END IF;
END
$compatibility$;

SELECT json_build_object(
    'validation', 'oban-postgresql-v14-schema-compatibility',
    'obanVersion', 14,
    'jobsLogged', true,
    'peersUnlogged', true,
    'legacyNotifierObjects', false,
    'passed', true
)::text;
SQL
