BEGIN;
CREATE TABLE IF NOT EXISTS product.audit_display_requests (
    request_id uuid PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES product.audit_drafts (id),
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    display_values jsonb NOT NULL CHECK (
        jsonb_typeof(display_values) = 'object'
        AND display_values ?& ARRAY['customer', 'model', 'part_number', 'part']
        AND display_values - ARRAY['customer', 'model', 'part_number', 'part'] = '{}'::jsonb
        AND jsonb_typeof(display_values->'customer') IN ('string', 'null')
        AND jsonb_typeof(display_values->'model') IN ('string', 'null')
        AND jsonb_typeof(display_values->'part_number') IN ('string', 'null')
        AND jsonb_typeof(display_values->'part') IN ('string', 'null')
    ),
    state text NOT NULL CHECK (state IN ('saved', 'conflict', 'rejected')),
    conflict_info jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((state = 'conflict') = (conflict_info IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS audit_display_revision
    ON product.audit_display_requests (task_id, round_id, file_version_id, expected_revision)
    WHERE state = 'saved';
GRANT SELECT, INSERT ON product.audit_display_requests TO ppap_api;
REVOKE UPDATE, DELETE ON product.audit_display_requests FROM ppap_api;
INSERT INTO product.schema_migrations (version) VALUES ('0024_display_corrections.sql') ON CONFLICT DO NOTHING;
COMMIT;
