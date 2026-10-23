BEGIN;
-- One refresh owner per provider authorization, shared by browser and MCP.
-- New sign-in credentials create a new owner; grants never follow user-wide
-- "latest credentials" pointers. Deploy with no old API process still running.
CREATE TABLE IF NOT EXISTS product.provider_authorizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES product.users(id),
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    provider text NOT NULL,
    app_id text NOT NULL,
    subject text NOT NULL,
    credential_hash text NOT NULL,
    access_token text NOT NULL DEFAULT '',
    refresh_token text NOT NULL DEFAULT '',
    access_expires_at timestamptz,
    refresh_expires_at timestamptz,
    state text NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'refreshing', 'reauthorize')),
    refresh_claim uuid,
    refresh_started_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, tenant_id, provider, app_id, subject, credential_hash)
);
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS provider_authorization_id uuid REFERENCES product.provider_authorizations(id);
ALTER TABLE product.mcp_grants ADD COLUMN IF NOT EXISTS provider_authorization_id uuid REFERENCES product.provider_authorizations(id);

-- Old browser rows did not record app/subject. Migrate only an unambiguous
-- identity; never choose an arbitrary identity for an existing session.
CREATE TEMP TABLE provider_session_binding ON COMMIT DROP AS
SELECT s.token_hash, s.user_id, s.tenant_id, i.provider, i.app_id, i.subject,
       coalesce(s.access_token, '') AS access_token, coalesce(s.refresh_token, '') AS refresh_token,
       s.access_expires_at, s.refresh_expires_at,
       encode(sha256(convert_to(coalesce(nullif(s.refresh_token, ''), nullif(s.access_token, ''), s.token_hash), 'UTF8')), 'hex') AS credential_hash
FROM product.sessions s
JOIN product.tenants t ON t.id=s.tenant_id
JOIN product.identities i ON i.user_id=s.user_id AND i.provider=t.provider AND i.tenant_key=t.external_key
WHERE s.provider_authorization_id IS NULL AND s.revoked_at IS NULL
AND 1=(SELECT count(*) FROM product.identities j WHERE j.user_id=s.user_id AND j.provider=t.provider AND j.tenant_key=t.external_key);

INSERT INTO product.provider_authorizations(user_id, tenant_id, provider, app_id, subject,
    credential_hash, access_token, refresh_token, access_expires_at, refresh_expires_at)
SELECT DISTINCT ON (user_id, tenant_id, provider, app_id, subject, credential_hash)
    user_id, tenant_id, provider, app_id, subject, credential_hash,
    access_token, refresh_token, access_expires_at, refresh_expires_at
FROM provider_session_binding ORDER BY user_id, tenant_id, provider, app_id, subject, credential_hash, access_expires_at DESC
ON CONFLICT DO NOTHING;
UPDATE product.sessions s SET provider_authorization_id=a.id
FROM provider_session_binding b JOIN product.provider_authorizations a
ON a.user_id=b.user_id AND a.tenant_id=b.tenant_id AND a.provider=b.provider AND a.app_id=b.app_id
AND a.subject=b.subject AND a.credential_hash=b.credential_hash
WHERE s.token_hash=b.token_hash;

-- The pre-release MCP candidate copied credentials. Preserve only exact
-- matches to a browser authorization; uncertain old copies require consent.
UPDATE product.mcp_grants g SET provider_authorization_id=a.id
FROM product.provider_authorizations a
WHERE g.provider_authorization_id IS NULL AND g.user_id=a.user_id AND g.tenant_id=a.tenant_id
AND g.provider=a.provider AND g.app_id=a.app_id AND g.subject=a.subject
AND coalesce(g.access_token, '')=a.access_token AND coalesce(g.refresh_token, '')=a.refresh_token
AND g.provider_refresh_state='ready';
-- An older MCP process may already have consumed this shared refresh token.
-- Revoking its client copy alone must not make the browser copy retryable.
UPDATE product.provider_authorizations a SET state='reauthorize'
FROM product.mcp_grants g
WHERE g.user_id=a.user_id AND g.tenant_id=a.tenant_id AND g.provider=a.provider
AND g.app_id=a.app_id AND g.subject=a.subject AND g.refresh_token=a.refresh_token
AND a.refresh_token<>'' AND g.provider_refresh_state<>'ready';
UPDATE product.sessions SET revoked_at=now() WHERE provider_authorization_id IS NULL AND revoked_at IS NULL;
UPDATE product.mcp_grants SET revoked_at=now() WHERE provider_authorization_id IS NULL AND revoked_at IS NULL;
-- Keep the old nullable columns for schema rollback, without credential copies.
UPDATE product.sessions SET access_token=NULL, refresh_token=NULL, access_expires_at=NULL, refresh_expires_at=NULL;
UPDATE product.mcp_grants SET access_token=NULL, refresh_token=NULL, access_expires_at=NULL, refresh_expires_at=NULL;
CREATE INDEX IF NOT EXISTS sessions_provider_authorization_idx ON product.sessions(provider_authorization_id);
CREATE INDEX IF NOT EXISTS mcp_grants_provider_authorization_idx ON product.mcp_grants(provider_authorization_id);
REVOKE ALL ON product.provider_authorizations FROM ppap_worker;
INSERT INTO product.schema_migrations(version) VALUES ('0035_provider_authorizations.sql') ON CONFLICT DO NOTHING;
COMMIT;
