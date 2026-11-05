BEGIN;

-- Actual Deep output is not the Suite that happened to be its starting point.
CREATE TABLE product.checker_round_artifacts (
    round_id uuid NOT NULL REFERENCES product.audit_rounds(id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions(id),
    result_attempt integer NOT NULL CHECK (result_attempt > 0),
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    suite_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    program_digest text NOT NULL CHECK (program_digest ~ '^[a-f0-9]{64}$'),
    input_digest text NOT NULL CHECK (input_digest ~ '^[a-f0-9]{64}$'),
    PRIMARY KEY (round_id, file_version_id, result_attempt)
);

CREATE TABLE product.checker_feedback (
    id uuid PRIMARY KEY,
    operation_id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    created_by uuid NOT NULL REFERENCES product.users(id),
    round_id uuid NOT NULL REFERENCES product.audit_rounds(id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions(id),
    rule_version_id uuid NOT NULL REFERENCES product.rule_versions(id),
    rule_clause_id text NOT NULL CHECK (rule_clause_id <> ''),
    result_attempt integer NOT NULL CHECK (result_attempt > 0),
    original_status text NOT NULL CHECK (original_status IN ('PASS','NG','NA','UNABLE','ERR')),
    suite_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    suite_key text NOT NULL,
    ordinal bigint NOT NULL CHECK (ordinal > 0),
    expected_status text NOT NULL CHECK (expected_status IN ('PASS','NG','NA')),
    rationale text NOT NULL CHECK (rationale <> ''),
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array'),
    request_fingerprint text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, operation_id),
    UNIQUE (tenant_id, suite_key, ordinal)
);
CREATE INDEX checker_feedback_suite ON product.checker_feedback(tenant_id, suite_key, ordinal) WHERE deleted_at IS NULL;

CREATE TABLE product.checker_optimizations (
    operation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    initiating_user_id uuid NOT NULL REFERENCES product.users(id),
    suite_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    action text NOT NULL CHECK (action IN ('save_only','run_tests')),
    request_fingerprint text NOT NULL,
    frozen jsonb NOT NULL CHECK (jsonb_typeof(frozen) = 'object'),
    job_id uuid UNIQUE REFERENCES engine.jobs(id),
    state text NOT NULL CHECK (state IN ('saved','queued','running','completed','failed','unknown','stopped')),
    model_started_at timestamptz,
    cancel_requested boolean NOT NULL DEFAULT false,
    candidate_revision_id uuid REFERENCES product.suite_revisions(id),
    report jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE INDEX checker_optimizations_owner ON product.checker_optimizations(tenant_id, initiating_user_id, created_at DESC);

GRANT SELECT, INSERT ON product.checker_round_artifacts TO ppap_api, ppap_worker;
GRANT SELECT, INSERT, UPDATE ON product.checker_feedback TO ppap_api;
GRANT SELECT ON product.checker_feedback TO ppap_worker;
GRANT SELECT, INSERT, UPDATE ON product.checker_optimizations TO ppap_api, ppap_worker;
-- Trusted completion creates an unadopted candidate, never a publication.
GRANT INSERT ON product.suite_objects, product.suite_revisions TO ppap_worker;
GRANT SELECT ON product.memberships, product.resources, product.resource_grants TO ppap_worker;
GRANT SELECT (id) ON product.tenants TO ppap_worker;
GRANT SELECT (task_fingerprint) ON product.audit_drafts TO ppap_worker;
GRANT SELECT ON product.deep_rule_changes TO ppap_worker;
INSERT INTO product.schema_migrations(version) VALUES ('0045_checker_development.sql') ON CONFLICT DO NOTHING;
COMMIT;
