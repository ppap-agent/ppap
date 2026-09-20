CREATE TABLE IF NOT EXISTS product.report_exports (
    id uuid PRIMARY KEY,
    round_id uuid NOT NULL REFERENCES product.audit_rounds(id),
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    created_by uuid NOT NULL REFERENCES product.users(id),
    fingerprint text NOT NULL,
    status text NOT NULL CHECK (status IN ('creating', 'writing', 'ready', 'unknown')),
    document_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (round_id, created_by, fingerprint)
);
INSERT INTO product.schema_migrations (version) VALUES ('0012_report_exports.sql') ON CONFLICT DO NOTHING;
