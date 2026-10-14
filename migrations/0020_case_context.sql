-- User registration context accompanies the frozen inputs; workers do not use it as evidence.
BEGIN;
ALTER TABLE product.rule_versions ADD COLUMN IF NOT EXISTS case_context jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE product.audit_drafts ADD COLUMN IF NOT EXISTS case_context jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE product.audit_rounds ADD COLUMN IF NOT EXISTS case_context jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE product.reviews ADD COLUMN IF NOT EXISTS citations jsonb NOT NULL DEFAULT '[]'::jsonb;
-- A human can review an unsupported clause without inventing a machine finding.
ALTER TABLE product.reviews DROP CONSTRAINT IF EXISTS reviews_finding_fk;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reviews_round_fk' AND conrelid = 'product.reviews'::regclass) THEN
    ALTER TABLE product.reviews ADD CONSTRAINT reviews_round_fk FOREIGN KEY (round_id) REFERENCES product.audit_rounds(id);
  END IF;
END $$;
INSERT INTO product.schema_migrations (version) VALUES ('0020_case_context.sql') ON CONFLICT DO NOTHING;
COMMIT;
