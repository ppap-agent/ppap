CREATE TABLE IF NOT EXISTS engine.jobs (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    audit_round_id uuid,
    kind text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL,
    attempt integer NOT NULL DEFAULT 0,
    lease_owner text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jobs_status_known CHECK (status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS engine.attempts (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES engine.jobs (id),
    attempt integer NOT NULL,
    worker_id text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    outcome text,
    diagnostic jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT attempts_job_attempt UNIQUE (job_id, attempt)
);

CREATE TABLE IF NOT EXISTS engine.worker_heartbeats (
    worker_id text PRIMARY KEY,
    started_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    git_sha text NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS jobs_claim_idx
    ON engine.jobs (created_at)
    WHERE status = 'queued';

GRANT SELECT, INSERT ON engine.jobs TO ppap_api;
GRANT SELECT ON engine.attempts TO ppap_api;
GRANT SELECT ON engine.worker_heartbeats TO ppap_api;
GRANT SELECT, INSERT, UPDATE ON engine.jobs TO ppap_worker;
GRANT SELECT, INSERT, UPDATE ON engine.attempts TO ppap_worker;
GRANT SELECT, INSERT, UPDATE ON engine.worker_heartbeats TO ppap_worker;
