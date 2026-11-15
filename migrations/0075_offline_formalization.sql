BEGIN;

-- Clause formalization is an offline developer tool; delivered rules are already
-- corrected, so the product keeps no readings, confirmations or reading jobs.
-- The runner replays every migration: 0060-0069 recreate these objects and this
-- file drops them again, so every apply ends without them.
DROP TABLE IF EXISTS product.rule_formalization_confirmations;
DROP TABLE IF EXISTS product.rule_formalization_operations;
DROP TABLE IF EXISTS product.rule_formalizations;
DROP FUNCTION IF EXISTS product.rule_formalization_belongs();
DROP FUNCTION IF EXISTS product.rule_formalizations_immutable();
DROP FUNCTION IF EXISTS product.rule_formalization_confirmation_pointer();

-- Reading jobs and their "readings are ready" notifications; attempts first.
DELETE FROM engine.attempts a USING engine.jobs j
WHERE a.job_id = j.id
  AND (j.kind = 'rule_formalization' OR (j.kind = 'feishu_notification' AND j.payload->>'stage' = 'formalization_ready'));
DELETE FROM engine.jobs
WHERE kind = 'rule_formalization' OR (kind = 'feishu_notification' AND payload->>'stage' = 'formalization_ready');

-- Official Suite upgrades and their per-member notices have no command left;
-- the revisions an upgrade saved stay ordinary Suite revisions.
DROP TABLE IF EXISTS product.suite_upgrade_operations;
DROP TABLE IF EXISTS product.suite_official_update_views;
DROP FUNCTION IF EXISTS product.validate_suite_upgrade_operation();
DROP FUNCTION IF EXISTS product.validate_suite_official_update_view();

INSERT INTO product.schema_migrations (version) VALUES ('0075_offline_formalization.sql') ON CONFLICT DO NOTHING;
COMMIT;
