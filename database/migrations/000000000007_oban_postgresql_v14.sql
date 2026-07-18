-- Flattened from Oban v2.23.0 PostgreSQL migrations V01..V14.
-- Compatibility is validated by comparing pg_catalog/pg_dump output against
-- Oban.Migrations.up(prefix: "oban") before publication. LIQI grants and
-- authority comments below are additive and do not alter Oban's storage ABI.
CREATE SCHEMA oban AUTHORIZATION liqi_owner;
REVOKE ALL ON SCHEMA oban FROM PUBLIC;

CREATE TYPE oban.oban_job_state AS ENUM (
    'available',
    'suspended',
    'scheduled',
    'executing',
    'retryable',
    'completed',
    'discarded',
    'cancelled'
);

CREATE TABLE oban.oban_jobs (
    id bigint NOT NULL,
    state oban.oban_job_state DEFAULT 'available'::oban.oban_job_state NOT NULL,
    queue text DEFAULT 'default'::text NOT NULL,
    worker text NOT NULL,
    args jsonb DEFAULT '{}'::jsonb NOT NULL,
    errors jsonb[] DEFAULT ARRAY[]::jsonb[] NOT NULL,
    attempt integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 20 NOT NULL,
    inserted_at timestamp without time zone DEFAULT timezone('UTC'::text, now()) NOT NULL,
    scheduled_at timestamp without time zone DEFAULT timezone('UTC'::text, now()) NOT NULL,
    attempted_at timestamp without time zone,
    completed_at timestamp without time zone,
    attempted_by text[],
    discarded_at timestamp without time zone,
    priority integer DEFAULT 0 NOT NULL,
    tags text[] DEFAULT ARRAY[]::text[],
    meta jsonb DEFAULT '{}'::jsonb,
    cancelled_at timestamp without time zone,
    CONSTRAINT attempt_range CHECK (attempt >= 0 AND attempt <= max_attempts),
    CONSTRAINT positive_max_attempts CHECK (max_attempts > 0),
    CONSTRAINT queue_length CHECK (char_length(queue) > 0 AND char_length(queue) < 128),
    CONSTRAINT worker_length CHECK (char_length(worker) > 0 AND char_length(worker) < 128)
);

CREATE SEQUENCE oban.oban_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE oban.oban_jobs_id_seq OWNED BY oban.oban_jobs.id;
ALTER TABLE ONLY oban.oban_jobs
    ALTER COLUMN id SET DEFAULT nextval('oban.oban_jobs_id_seq'::regclass);
ALTER TABLE oban.oban_jobs
    ADD CONSTRAINT non_negative_priority CHECK (priority >= 0) NOT VALID;
ALTER TABLE ONLY oban.oban_jobs
    ADD CONSTRAINT oban_jobs_pkey PRIMARY KEY (id);

CREATE UNLOGGED TABLE oban.oban_peers (
    name text NOT NULL,
    node text NOT NULL,
    started_at timestamp without time zone NOT NULL,
    expires_at timestamp without time zone NOT NULL
);
ALTER TABLE ONLY oban.oban_peers
    ADD CONSTRAINT oban_peers_pkey PRIMARY KEY (name);

CREATE INDEX oban_jobs_args_index
ON oban.oban_jobs USING gin (args);
CREATE INDEX oban_jobs_meta_index
ON oban.oban_jobs USING gin (meta);
CREATE INDEX oban_jobs_state_cancelled_at_index
ON oban.oban_jobs (state, cancelled_at);
CREATE INDEX oban_jobs_state_discarded_at_index
ON oban.oban_jobs (state, discarded_at);
CREATE INDEX oban_jobs_state_queue_priority_scheduled_at_id_index
ON oban.oban_jobs (state, queue, priority, scheduled_at, id);

COMMENT ON TABLE oban.oban_jobs IS '14';

REVOKE ALL ON ALL TABLES IN SCHEMA oban FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA oban FROM PUBLIC;
REVOKE ALL ON TYPE oban.oban_job_state FROM PUBLIC;

GRANT USAGE ON SCHEMA oban TO liqi_worker;
GRANT USAGE ON TYPE oban.oban_job_state TO liqi_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON oban.oban_jobs, oban.oban_peers TO liqi_worker;
GRANT USAGE, SELECT ON SEQUENCE oban.oban_jobs_id_seq TO liqi_worker;

COMMENT ON SCHEMA oban IS
    'Oban durable work storage. It is not the domain outbox, realtime bus, or an exactly-once executor.';
COMMENT ON COLUMN oban.oban_jobs.args IS
    'Oban job arguments. Domain event authority remains platform.outbox_events; this payload may only describe bounded durable work.';
COMMENT ON TABLE oban.oban_peers IS
    'Unlogged rebuildable Oban peer coordination state; job authority remains in logged oban_jobs.';
