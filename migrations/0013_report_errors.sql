ALTER TABLE product.report_exports ADD COLUMN IF NOT EXISTS error_code text;
ALTER TABLE product.report_exports DROP CONSTRAINT IF EXISTS report_exports_status_check;
ALTER TABLE product.report_exports ADD CONSTRAINT report_exports_status_check
    CHECK (status IN ('creating', 'writing', 'ready', 'unknown', 'failed'));
INSERT INTO product.schema_migrations (version) VALUES ('0013_report_errors.sql') ON CONFLICT DO NOTHING;
