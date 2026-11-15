BEGIN;

-- Checker history import is retired: every remaining Round has a current Core
-- admission snapshot, and no archive Suite revision exists. The runner replays
-- every migration: 0052 recreates these objects and this file drops them again,
-- so every apply ends without them.
DROP TRIGGER IF EXISTS check_history_import ON product.round_inputs;
DROP FUNCTION IF EXISTS product.check_history_import();
ALTER TABLE product.round_inputs DROP CONSTRAINT IF EXISTS checker_history_shape;
ALTER TABLE product.round_inputs DROP COLUMN IF EXISTS checker_history;

DROP TRIGGER IF EXISTS reject_history_release ON product.tenant_suite_heads;
DROP TRIGGER IF EXISTS reject_history_release ON product.suite_validations;
DROP TRIGGER IF EXISTS reject_history_release ON product.suite_publications;
DROP FUNCTION IF EXISTS product.reject_history_release();
DROP TRIGGER IF EXISTS check_history_suite ON product.suite_revisions;
DROP FUNCTION IF EXISTS product.check_history_suite();
DROP INDEX IF EXISTS product.suite_historical_content;
ALTER TABLE product.suite_revisions DROP COLUMN IF EXISTS history_only;

INSERT INTO product.schema_migrations (version) VALUES ('0076_retire_checker_history.sql') ON CONFLICT DO NOTHING;
COMMIT;
