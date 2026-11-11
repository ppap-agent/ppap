BEGIN;

-- Check points may be the clause text tidied without changing its meaning. Such a
-- reading has no OWL or CLIF. Existing OWL readings keep their content and stay valid.
ALTER TABLE product.rule_formalizations DROP CONSTRAINT IF EXISTS rule_formalizations_formal_profile_check;
ALTER TABLE product.rule_formalizations DROP CONSTRAINT IF EXISTS rule_formalizations_owl_content_check;
ALTER TABLE product.rule_formalizations DROP CONSTRAINT IF EXISTS rule_formalizations_check;
ALTER TABLE product.rule_formalizations DROP CONSTRAINT IF EXISTS rule_formalizations_profile_content;
ALTER TABLE product.rule_formalizations ALTER COLUMN owl_content DROP NOT NULL;
ALTER TABLE product.rule_formalizations ADD CONSTRAINT rule_formalizations_profile_content CHECK (
    (formal_profile = 'rule-text-v1' AND owl_content IS NULL AND clif_content IS NULL)
    OR (formal_profile = 'owl2-dl-v1' AND char_length(owl_content) BETWEEN 1 AND 65536 AND clif_content IS NULL)
    OR (formal_profile = 'owl2-plus-clif-v1' AND char_length(owl_content) BETWEEN 1 AND 65536)
);

INSERT INTO product.schema_migrations (version) VALUES ('0064_rule_text_check_points.sql') ON CONFLICT DO NOTHING;
COMMIT;
