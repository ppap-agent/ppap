BEGIN;

-- A person may reject a clause, or parts of it, that states no programmatically checkable standard.
-- The rejection, with its reason, belongs to the immutable reading and is confirmed like any reading.
ALTER TABLE product.rule_formalizations ADD COLUMN IF NOT EXISTS rejections jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE product.rule_formalizations DROP CONSTRAINT IF EXISTS rule_formalizations_rejections;
ALTER TABLE product.rule_formalizations ADD CONSTRAINT rule_formalizations_rejections CHECK (jsonb_typeof(rejections) = 'array');

INSERT INTO product.schema_migrations (version) VALUES ('0068_formalization_rejections.sql') ON CONFLICT DO NOTHING;
COMMIT;
