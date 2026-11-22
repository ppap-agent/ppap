BEGIN;

-- Tables nothing reads or writes any more. The runner replays every migration:
-- 0031 and 0047 recreate them and this file drops them again, so every apply ends without them.

-- Candidate save receipts: tenant Suite revisions are delivered by the platform through
-- the Suite store; the revisions saved so far stay ordinary Suite revisions.
DROP TABLE IF EXISTS product.suite_candidate_operations;
DROP FUNCTION IF EXISTS product.validate_suite_candidate_operation();

-- Template feedback has no route, job or reader.
DROP TABLE IF EXISTS product.template_feedback;

INSERT INTO product.schema_migrations (version) VALUES ('0087_drop_unused_tables.sql') ON CONFLICT DO NOTHING;
COMMIT;
