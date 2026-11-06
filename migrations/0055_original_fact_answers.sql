BEGIN;

-- Original-file answers never replace product.findings or rule appendices.
CREATE TABLE IF NOT EXISTS product.original_fact_answers (
    operation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    round_id uuid NOT NULL,
    file_version_id uuid NOT NULL,
    field text NOT NULL CHECK (length(field) BETWEEN 1 AND 256),
    revision integer NOT NULL CHECK (revision > 0),
    request jsonb NOT NULL,
    answer jsonb NOT NULL,
    receipt jsonb NOT NULL,
    answered_by uuid NOT NULL REFERENCES product.users(id),
    answered_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (round_id,file_version_id) REFERENCES product.round_inputs(round_id,file_version_id),
    UNIQUE (round_id,file_version_id,field),
    UNIQUE (round_id,revision),
    CHECK (jsonb_typeof(request) = 'object' AND jsonb_typeof(answer) = 'object' AND jsonb_typeof(receipt) = 'object')
);
REVOKE UPDATE ON product.original_fact_answers FROM ppap_api;
GRANT SELECT, INSERT ON product.original_fact_answers TO ppap_api;

INSERT INTO product.schema_migrations(version) VALUES ('0055_original_fact_answers.sql') ON CONFLICT DO NOTHING;
COMMIT;
