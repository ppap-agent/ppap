CREATE TABLE IF NOT EXISTS product.tenants (
    id uuid PRIMARY KEY,
    provider text NOT NULL,
    external_key text NOT NULL,
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tenants_external UNIQUE (provider, external_key)
);

CREATE TABLE IF NOT EXISTS product.users (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product.identities (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES product.users (id),
    provider text NOT NULL,
    app_id text NOT NULL,
    tenant_key text NOT NULL,
    subject text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT identities_binding UNIQUE (provider, app_id, tenant_key, subject)
);

CREATE TABLE IF NOT EXISTS product.memberships (
    user_id uuid NOT NULL REFERENCES product.users (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    role text NOT NULL,
    PRIMARY KEY (user_id, tenant_id),
    CONSTRAINT membership_role_known CHECK (role IN ('member'))
);

CREATE TABLE IF NOT EXISTS product.sessions (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES product.users (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    access_token text,
    refresh_token text,
    access_expires_at timestamptz,
    refresh_expires_at timestamptz
);

CREATE TABLE IF NOT EXISTS product.oauth_states (
    state_hash text PRIMARY KEY,
    return_to text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz
);

CREATE TABLE IF NOT EXISTS product.app_tickets (
    provider text NOT NULL,
    app_id text NOT NULL,
    ticket text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, app_id)
);

CREATE TABLE IF NOT EXISTS product.resources (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    kind text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product.resource_grants (
    resource_id uuid NOT NULL REFERENCES product.resources (id),
    user_id uuid NOT NULL REFERENCES product.users (id),
    capability text NOT NULL,
    PRIMARY KEY (resource_id, user_id, capability),
    CONSTRAINT grant_capability_known CHECK (capability IN ('read', 'review'))
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON product.sessions (user_id);
CREATE INDEX IF NOT EXISTS identities_user_idx ON product.identities (user_id);
CREATE INDEX IF NOT EXISTS resources_tenant_idx ON product.resources (tenant_id);

GRANT SELECT, INSERT, UPDATE ON
    product.tenants,
    product.users,
    product.identities,
    product.memberships,
    product.sessions,
    product.oauth_states,
    product.app_tickets,
    product.resources,
    product.resource_grants
TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0003_identity.sql')
ON CONFLICT DO NOTHING;
