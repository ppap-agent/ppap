ALTER TABLE product.round_inputs
    ADD COLUMN IF NOT EXISTS rule_execution jsonb;

INSERT INTO product.schema_migrations (version) VALUES ('0016_rule_execution.sql') ON CONFLICT DO NOTHING;
