BEGIN;

-- Advisory dispositions are separate from machine conclusions and human judgments.
ALTER TABLE product.findings ADD COLUMN IF NOT EXISTS review_hint_action jsonb;
ALTER TABLE product.findings DROP CONSTRAINT IF EXISTS review_hint_action_known;
ALTER TABLE product.findings ADD CONSTRAINT review_hint_action_known CHECK (
    review_hint_action IS NULL OR
    (jsonb_typeof(review_hint_action) = 'object' AND review_hint_action->>'action' IN ('accepted', 'ignored'))
);
ALTER TABLE product.audit_rounds ADD COLUMN IF NOT EXISTS review_document_pairs jsonb NOT NULL DEFAULT '[]'::jsonb;
INSERT INTO product.schema_migrations (version) VALUES ('0056_review_hint_actions.sql') ON CONFLICT DO NOTHING;

COMMIT;
