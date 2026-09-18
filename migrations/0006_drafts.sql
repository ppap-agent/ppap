CREATE TABLE IF NOT EXISTS product.rule_versions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    project_id text NOT NULL,
    engine_rule_id text NOT NULL,
    document_type text NOT NULL,
    label text NOT NULL,
    content jsonb NOT NULL,
    content_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rule_versions_sha CHECK (char_length(content_sha256) = 64),
    CONSTRAINT rule_versions_unique_snapshot UNIQUE (tenant_id, project_id, engine_rule_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS product.audit_drafts (
    id uuid PRIMARY KEY REFERENCES product.resources (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    status text NOT NULL DEFAULT 'open',
    project_id text,
    label text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT drafts_status_known CHECK (status IN ('open'))
);

CREATE TABLE IF NOT EXISTS product.draft_items (
    id uuid PRIMARY KEY,
    draft_id uuid NOT NULL REFERENCES product.audit_drafts (id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    file_id uuid NOT NULL REFERENCES product.files (id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions (id),
    role text NOT NULL,
    position integer NOT NULL,
    identification_status text NOT NULL DEFAULT 'pending',
    identification_job_id uuid,
    matched_document_type text,
    product_rule_version_id uuid REFERENCES product.rule_versions (id),
    engine_rule_id text,
    bound_content_sha256 text,
    bound_check_count integer,
    match_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT draft_item_role_known CHECK (role IN ('target', 'reference')),
    CONSTRAINT draft_item_ident_known CHECK (identification_status IN (
        'pending', 'unrecognized', 'unsupported', 'ambiguous', 'uncertain', 'matched', 'unknown_project'
    )),
    CONSTRAINT draft_items_unique_version UNIQUE (draft_id, file_version_id),
    CONSTRAINT draft_items_bound_sha CHECK (
        bound_content_sha256 IS NULL OR char_length(bound_content_sha256) = 64
    )
);

CREATE INDEX IF NOT EXISTS rule_versions_tenant_project_idx
    ON product.rule_versions (tenant_id, project_id, engine_rule_id, created_at);
CREATE INDEX IF NOT EXISTS audit_drafts_tenant_idx ON product.audit_drafts (tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS draft_items_draft_idx ON product.draft_items (draft_id, position);

GRANT SELECT, INSERT, UPDATE, DELETE ON product.rule_versions, product.audit_drafts, product.draft_items TO ppap_api;
GRANT SELECT ON product.rule_versions, product.audit_drafts TO ppap_worker;
GRANT SELECT, UPDATE ON product.draft_items TO ppap_worker;
