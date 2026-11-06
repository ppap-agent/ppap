-- Human-selected reason category belongs to the answer; grouping stays read-time only.
ALTER TABLE product.checker_feedback ADD COLUMN IF NOT EXISTS reason_category text NOT NULL DEFAULT 'unspecified'
    CHECK (reason_category IN ('unspecified', 'missing_value', 'incorrect_reading', 'rule_application'));
CREATE INDEX IF NOT EXISTS audit_rounds_tenant_id_cursor ON product.audit_rounds (tenant_id, id);
INSERT INTO product.schema_migrations(version) VALUES ('0057_checker_feedback_categories.sql') ON CONFLICT DO NOTHING;
