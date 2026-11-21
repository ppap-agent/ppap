BEGIN;

-- An audit task prepared from a Feishu folder, before and beside its audit task: the folder's manifest, each
-- file's import and the user's decisions. `id` is the task's ID everywhere: the request that created it and the
-- audit task it creates. `state` only changes with `revision`, so a stale writer loses and re-reads.
CREATE TABLE IF NOT EXISTS product.audit_workflows (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    created_by uuid NOT NULL REFERENCES product.users(id),
    request_fingerprint text NOT NULL,
    source_ref text NOT NULL,
    state jsonb NOT NULL,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_workflows_source ON product.audit_workflows (tenant_id, source_ref, created_at DESC);
GRANT SELECT, INSERT, UPDATE ON product.audit_workflows TO ppap_api;
REVOKE ALL ON product.audit_workflows FROM ppap_worker;

-- MCP no longer moves bytes: files come from Feishu and results go back to Feishu.
DROP TABLE IF EXISTS product.mcp_transfers;

INSERT INTO product.schema_migrations (version) VALUES ('0084_audit_workflows.sql') ON CONFLICT DO NOTHING;
COMMIT;
