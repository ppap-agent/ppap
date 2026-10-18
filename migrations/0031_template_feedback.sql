BEGIN;
CREATE TABLE IF NOT EXISTS product.template_feedback (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    created_by uuid NOT NULL REFERENCES product.users(id),
    request_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    phase text NOT NULL CHECK (phase IN ('preparing', 'dispatching', 'unknown', 'received', 'failed')),
    payload jsonb NOT NULL,
    submission_id text,
    reason text,
    received_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, created_by, request_id, attempt_id),
    UNIQUE (tenant_id, created_by, request_id, attempt_number),
    CHECK ((phase = 'received') = (submission_id IS NOT NULL AND received_at IS NOT NULL))
);
GRANT SELECT, INSERT, UPDATE ON product.template_feedback TO ppap_api;
INSERT INTO product.schema_migrations(version) VALUES ('0031_template_feedback.sql') ON CONFLICT DO NOTHING;
COMMIT;
