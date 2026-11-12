BEGIN;

-- There is one kind of audit (#322). Deep rounds, their rule-change receipts and
-- generated round artifacts were removed from production before this migration.
ALTER TABLE product.audit_drafts DROP CONSTRAINT IF EXISTS draft_analysis_mode_known;
ALTER TABLE product.audit_rounds DROP CONSTRAINT IF EXISTS round_analysis_mode_known;
ALTER TABLE product.audit_drafts DROP COLUMN IF EXISTS analysis_mode;
ALTER TABLE product.audit_rounds DROP COLUMN IF EXISTS analysis_mode;
DROP TABLE IF EXISTS product.deep_rule_changes;
DROP TABLE IF EXISTS product.checker_round_artifacts;

-- Saved upload preparations no longer carry a mode.
UPDATE product.audit_drafts
SET case_context = jsonb_set(case_context, '{_native_upload_preparation}', (case_context -> '_native_upload_preparation') - 'analysis_mode')
WHERE case_context -> '_native_upload_preparation' ? 'analysis_mode';

INSERT INTO product.schema_migrations (version) VALUES ('0070_single_audit_kind.sql') ON CONFLICT DO NOTHING;
COMMIT;
