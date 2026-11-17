BEGIN;

-- Audit data has two rules: every member of an enterprise sees its drafts, rounds and files, and
-- only the person who created one changes it (created_by on audit_drafts, audit_rounds, files).
-- Per-user read/review grants no longer decide anything, so the table goes.
DROP TABLE IF EXISTS product.resource_grants;

INSERT INTO product.schema_migrations (version) VALUES ('0082_drop_resource_grants.sql') ON CONFLICT DO NOTHING;
COMMIT;
