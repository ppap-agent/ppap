BEGIN;

-- Predefined checker leaves moved from checkers/v5/<customer>/<group> to checkers/<customer>/<group>.
-- The stored path is the leaf's location, not part of its content digest; only that column changes.
ALTER TABLE product.suite_revisions DISABLE TRIGGER immutable_suite_revision;
UPDATE product.suite_revisions
   SET source_path = regexp_replace(source_path, '^v[0-9]+/', '')
 WHERE tenant_id IS NULL AND source_path ~ '^v[0-9]+/[^/]+/[^/]+$';
ALTER TABLE product.suite_revisions ENABLE TRIGGER immutable_suite_revision;

INSERT INTO product.schema_migrations (version) VALUES ('0066_company_checker_source_paths.sql') ON CONFLICT DO NOTHING;
COMMIT;
