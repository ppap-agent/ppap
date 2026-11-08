BEGIN;

-- Directory identity for an official immutable revision. Content digest stays
-- the file digest, so two identical leaves can each keep their own row.
-- Legacy rows stay NULL: the column is not backfilled and history is not rewritten.
ALTER TABLE product.suite_revisions ADD COLUMN IF NOT EXISTS source_path text;
ALTER TABLE product.suite_revisions DROP CONSTRAINT IF EXISTS suite_revision_source_path_shape;
ALTER TABLE product.suite_revisions ADD CONSTRAINT suite_revision_source_path_shape CHECK (
    source_path IS NULL OR (
        char_length(source_path) BETWEEN 1 AND 512
        AND source_path = btrim(source_path)
    )
);
CREATE INDEX IF NOT EXISTS suite_revisions_official_source_path
    ON product.suite_revisions (source_path, revision_number)
    WHERE tenant_id IS NULL AND source_path IS NOT NULL;

INSERT INTO product.schema_migrations(version) VALUES ('0059_predefined_source_path.sql') ON CONFLICT DO NOTHING;
COMMIT;
