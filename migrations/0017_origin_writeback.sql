CREATE TABLE IF NOT EXISTS product.file_version_sources (
    file_version_id uuid PRIMARY KEY REFERENCES product.file_versions (id),
    provider text NOT NULL,
    object_kind text NOT NULL,
    object_ref text NOT NULL
);
CREATE TABLE IF NOT EXISTS product.draft_writeback_targets (
    draft_id uuid PRIMARY KEY REFERENCES product.audit_drafts (id),
    provider text NOT NULL,
    object_kind text NOT NULL,
    object_ref text NOT NULL
);
CREATE TABLE IF NOT EXISTS product.round_writeback_targets (
    round_id uuid PRIMARY KEY REFERENCES product.audit_rounds (id),
    provider text NOT NULL,
    object_kind text NOT NULL,
    object_ref text NOT NULL
);
INSERT INTO product.schema_migrations (version) VALUES ('0017_origin_writeback.sql') ON CONFLICT DO NOTHING;
