-- Preserve machine uncertainty and non-applicability, not as fabricated NG/ERR.
ALTER TABLE product.findings DROP CONSTRAINT IF EXISTS finding_status_known;
ALTER TABLE product.findings ADD CONSTRAINT finding_status_known
    CHECK (status IN ('PASS', 'NG', 'ERR', 'UNABLE', 'NA'));
INSERT INTO product.schema_migrations (version) VALUES ('0019_semantic_findings.sql') ON CONFLICT DO NOTHING;
