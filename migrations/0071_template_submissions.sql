BEGIN;

-- A customer offers more document templates to the platform team ("补充模板").
-- The files are ordinary tenant file versions; this records which ones were offered, by whom.
-- The request id makes a repeated submission the same record. Operators read these rows.
CREATE TABLE IF NOT EXISTS product.template_submissions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES product.users(id),
    customer text CHECK (customer IS NULL OR char_length(customer) BETWEEN 1 AND 100),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS template_submissions_created ON product.template_submissions (created_at);

CREATE TABLE IF NOT EXISTS product.template_submission_files (
    submission_id uuid NOT NULL REFERENCES product.template_submissions(id) ON DELETE CASCADE,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 49),
    file_version_id uuid NOT NULL REFERENCES product.file_versions(id),
    PRIMARY KEY (submission_id, ordinal),
    UNIQUE (submission_id, file_version_id)
);

GRANT SELECT, INSERT ON product.template_submissions TO ppap_api;
GRANT SELECT, INSERT ON product.template_submission_files TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0071_template_submissions.sql') ON CONFLICT DO NOTHING;
COMMIT;
