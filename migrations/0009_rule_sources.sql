ALTER TABLE product.rule_versions
    ADD COLUMN IF NOT EXISTS source_file_version_id uuid REFERENCES product.file_versions (id),
    ADD COLUMN IF NOT EXISTS predecessor_id uuid REFERENCES product.rule_versions (id),
    ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES product.users (id);

ALTER TABLE engine.jobs ADD COLUMN IF NOT EXISTS result jsonb;

INSERT INTO product.schema_migrations (version) VALUES ('0009_rule_sources.sql')
ON CONFLICT DO NOTHING;
