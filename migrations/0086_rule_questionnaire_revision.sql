BEGIN;

-- A questionnaire is asked on one revision of a customer's rulebook (assets/<customer>/rulebook/<version>/m<revision>/).
-- `rule_version` names the rulebook and its version («规范性条款 v1.0»); the revision is its own field, so each revision
-- of a version carries its own questionnaire and the rules page links them by these fields, never by the label.
ALTER TABLE product.rule_questionnaires
    ADD COLUMN IF NOT EXISTS rulebook_version text,
    ADD COLUMN IF NOT EXISTS rulebook_revision integer;

-- The only questionnaire loaded so far was made on UAES rulebook v1.0 修订 0 (assets/uaes/clause-check-semantic-v2);
-- its ID, label, cards and answers stay as they are. Any other row stops this migration instead of being guessed.
UPDATE product.rule_questionnaires SET rulebook_version = 'v1.0', rulebook_revision = 0
WHERE customer_id = 'uaes' AND rule_version = '规范性条款 v1.0' AND rulebook_version IS NULL;

ALTER TABLE product.rule_questionnaires
    ALTER COLUMN rulebook_version SET NOT NULL,
    ALTER COLUMN rulebook_revision SET NOT NULL;
ALTER TABLE product.rule_questionnaires DROP CONSTRAINT IF EXISTS rule_questionnaires_rulebook;
ALTER TABLE product.rule_questionnaires ADD CONSTRAINT rule_questionnaires_rulebook CHECK (
    rulebook_version ~ '^v[0-9]+\.[0-9]+$' AND rulebook_revision >= 0 AND right(rule_version, length(rulebook_version) + 1) = ' ' || rulebook_version
);

-- One questionnaire per enterprise, customer and rulebook revision.
ALTER TABLE product.rule_questionnaires DROP CONSTRAINT IF EXISTS rule_questionnaires_tenant_id_customer_id_rule_version_key;
CREATE UNIQUE INDEX IF NOT EXISTS rule_questionnaires_revision
    ON product.rule_questionnaires (tenant_id, customer_id, rulebook_version, rulebook_revision);

INSERT INTO product.schema_migrations (version) VALUES ('0086_rule_questionnaire_revision.sql') ON CONFLICT DO NOTHING;
COMMIT;
