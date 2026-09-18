CREATE TABLE IF NOT EXISTS product.audit_rounds (
    id uuid PRIMARY KEY REFERENCES product.resources (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    draft_id uuid NOT NULL REFERENCES product.audit_drafts (id),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL,
    current_result_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rounds_status_known CHECK (status IN ('accepted', 'running', 'succeeded', 'failed')),
    CONSTRAINT rounds_idempotency UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT rounds_request_hash_sha CHECK (char_length(request_hash) = 64)
);

CREATE TABLE IF NOT EXISTS product.round_inputs (
    id uuid PRIMARY KEY,
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id) ON DELETE RESTRICT,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    file_id uuid NOT NULL REFERENCES product.files (id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions (id),
    role text NOT NULL,
    sha256 text NOT NULL,
    original_filename text NOT NULL,
    product_rule_version_id uuid REFERENCES product.rule_versions (id),
    engine_rule_id text,
    bound_content_sha256 text,
    bound_check_count integer,
    position integer NOT NULL,
    CONSTRAINT round_input_role_known CHECK (role IN ('target', 'reference')),
    CONSTRAINT round_inputs_sha CHECK (char_length(sha256) = 64)
);

CREATE TABLE IF NOT EXISTS product.round_results (
    id uuid PRIMARY KEY,
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id) ON DELETE RESTRICT,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    job_id uuid NOT NULL,
    attempt integer NOT NULL,
    file_version_id uuid NOT NULL,
    sha256 text NOT NULL,
    product_rule_version_id text,
    engine_rule_id text,
    findings jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT round_results_job_attempt UNIQUE (job_id, attempt)
);

CREATE INDEX IF NOT EXISTS audit_rounds_tenant_idx ON product.audit_rounds (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS round_inputs_round_idx ON product.round_inputs (round_id, position);
CREATE INDEX IF NOT EXISTS round_results_round_idx ON product.round_results (round_id, created_at);
CREATE INDEX IF NOT EXISTS jobs_lease_reclaim_idx
    ON engine.jobs (created_at)
    WHERE status = 'leased';

GRANT SELECT, INSERT, UPDATE ON product.audit_rounds TO ppap_api;
GRANT SELECT, INSERT ON product.round_inputs TO ppap_api;
GRANT SELECT ON product.round_results TO ppap_api;
GRANT UPDATE ON engine.jobs TO ppap_api;

GRANT SELECT, UPDATE ON product.audit_rounds TO ppap_worker;
GRANT SELECT ON product.round_inputs TO ppap_worker;
GRANT SELECT, INSERT ON product.round_results TO ppap_worker;
