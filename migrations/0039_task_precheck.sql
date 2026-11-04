BEGIN;
-- Content preparation reuses engine.jobs. Worker receives only the input columns
-- needed to freeze a Task snapshot, not identity/session or review authority.
GRANT SELECT (customer_preference, analysis_mode) ON product.audit_drafts TO ppap_worker;
GRANT SELECT (file_version_id, role, position) ON product.draft_items TO ppap_worker;
INSERT INTO product.schema_migrations (version) VALUES ('0039_task_precheck.sql') ON CONFLICT DO NOTHING;
COMMIT;
