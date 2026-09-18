CREATE TABLE IF NOT EXISTS product.files (
    id uuid PRIMARY KEY REFERENCES product.resources (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    original_filename text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product.file_versions (
    id uuid PRIMARY KEY,
    file_id uuid NOT NULL REFERENCES product.files (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    original_filename text NOT NULL,
    content_type text NOT NULL,
    byte_size bigint NOT NULL,
    sha256 text,
    object_key text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    finalized_at timestamptz,
    CONSTRAINT file_versions_status_known CHECK (status IN ('staging', 'ready', 'failed')),
    CONSTRAINT file_versions_object_key_unique UNIQUE (object_key),
    CONSTRAINT file_versions_ready_has_sha CHECK (
        status <> 'ready' OR (sha256 IS NOT NULL AND char_length(sha256) = 64)
    )
);

CREATE INDEX IF NOT EXISTS files_tenant_idx ON product.files (tenant_id);
CREATE INDEX IF NOT EXISTS file_versions_file_idx ON product.file_versions (file_id, created_at);

GRANT SELECT, INSERT, UPDATE ON product.files, product.file_versions TO ppap_api;
