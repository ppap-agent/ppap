ALTER TABLE engine.jobs
    ADD COLUMN IF NOT EXISTS result jsonb NOT NULL DEFAULT '{}'::jsonb;

GRANT USAGE ON SCHEMA product TO ppap_worker;
GRANT SELECT ON product.files, product.file_versions TO ppap_worker;
