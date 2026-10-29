BEGIN;
-- One receipt owns each explicit rule change. Existing rounds own calculations.
CREATE TABLE IF NOT EXISTS product.deep_rule_changes (
    operation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    workspace_round_id uuid NOT NULL REFERENCES product.audit_rounds (id),
    source_round_id uuid NOT NULL REFERENCES product.audit_rounds (id),
    analysis_round_id uuid REFERENCES product.audit_rounds (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    revision bigint NOT NULL CHECK (revision > 0),
    request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
    receipt jsonb NOT NULL CHECK (jsonb_typeof(receipt) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_round_id, revision)
);
CREATE INDEX IF NOT EXISTS deep_rule_change_analysis ON product.deep_rule_changes (analysis_round_id);
GRANT SELECT, INSERT ON product.deep_rule_changes TO ppap_api;
REVOKE UPDATE, DELETE ON product.deep_rule_changes FROM ppap_api;
INSERT INTO product.schema_migrations (version) VALUES ('0037_deep_feedback.sql') ON CONFLICT DO NOTHING;
COMMIT;
