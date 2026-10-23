BEGIN;
-- MCP owns delegation and transport receipts, never audit execution state.
CREATE TABLE IF NOT EXISTS product.mcp_clients (
    id text PRIMARY KEY,
    metadata jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS product.mcp_grants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id text NOT NULL REFERENCES product.mcp_clients(id),
    user_id uuid NOT NULL REFERENCES product.users(id),
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    provider text NOT NULL,
    app_id text NOT NULL,
    subject text NOT NULL,
    scopes text[] NOT NULL,
    resource text NOT NULL,
    access_token text,
    refresh_token text,
    access_expires_at timestamptz,
    refresh_expires_at timestamptz,
    provider_refresh_state text NOT NULL DEFAULT 'ready' CHECK (provider_refresh_state IN ('ready','refreshing','reauthorize')),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mcp_grants_user_idx ON product.mcp_grants(user_id, tenant_id);
CREATE TABLE IF NOT EXISTS product.mcp_authorizations (
    id text PRIMARY KEY,
    client_id text NOT NULL REFERENCES product.mcp_clients(id),
    redirect_uri text NOT NULL,
    resource text NOT NULL,
    scopes text[] NOT NULL,
    state text NOT NULL,
    challenge text NOT NULL,
    csrf_hash text,
    browser_hash text,
    code_hash text UNIQUE,
    grant_id uuid REFERENCES product.mcp_grants(id),
    expires_at timestamptz NOT NULL,
    decided_at timestamptz,
    consumed_at timestamptz
);
CREATE TABLE IF NOT EXISTS product.mcp_tokens (
    token_hash text PRIMARY KEY,
    grant_id uuid NOT NULL REFERENCES product.mcp_grants(id),
    kind text NOT NULL CHECK (kind IN ('access','refresh')),
    expires_at timestamptz NOT NULL,
    used_at timestamptz
);
CREATE INDEX IF NOT EXISTS mcp_tokens_grant_idx ON product.mcp_tokens(grant_id);
CREATE TABLE IF NOT EXISTS product.mcp_operations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    grant_id uuid NOT NULL REFERENCES product.mcp_grants(id),
    request_id uuid NOT NULL,
    tool text NOT NULL,
    arguments jsonb NOT NULL,
    fingerprint text NOT NULL,
    required_scope text NOT NULL,
    state text NOT NULL CHECK (state IN ('confirmation_required','ready','running','succeeded','failed','unknown','denied')),
    confirmation jsonb,
    confirmed_by uuid REFERENCES product.users(id),
    confirmed_at timestamptz,
    csrf_hash text,
    browser_hash text,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(grant_id, request_id)
);
CREATE TABLE IF NOT EXISTS product.mcp_transfers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    grant_id uuid NOT NULL REFERENCES product.mcp_grants(id),
    method text NOT NULL CHECK (method IN ('GET','PUT')),
    path text NOT NULL,
    required_scope text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS product.mcp_rate_limits (
    bucket text PRIMARY KEY,
    window_start timestamptz NOT NULL,
    requests integer NOT NULL
);
ALTER TABLE product.mcp_transfers ADD COLUMN IF NOT EXISTS parameters jsonb NOT NULL DEFAULT '{}';
REVOKE ALL ON product.mcp_clients, product.mcp_grants, product.mcp_authorizations,
    product.mcp_tokens, product.mcp_operations, product.mcp_transfers, product.mcp_rate_limits FROM ppap_worker;
INSERT INTO product.schema_migrations(version) VALUES ('0034_mcp.sql') ON CONFLICT DO NOTHING;
COMMIT;
