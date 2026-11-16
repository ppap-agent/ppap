BEGIN;

-- Feishu Project (Meego) plugin as a first-party host. A Meego identity is
-- bound to one existing Feishu provider authorization after the person
-- confirms it in a PPAP browser page. Host sessions reuse product.sessions and
-- are presented as Bearer tokens; they never act as browser cookies.
CREATE TABLE IF NOT EXISTS product.meego_bindings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plugin_id text NOT NULL,
    meego_tenant_key text NOT NULL,
    user_key text NOT NULL,
    meego_name text NOT NULL DEFAULT '',
    user_id uuid NOT NULL REFERENCES product.users (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    provider_authorization_id uuid NOT NULL REFERENCES product.provider_authorizations (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS meego_bindings_active
    ON product.meego_bindings (plugin_id, meego_tenant_key, user_key) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS meego_bindings_user ON product.meego_bindings (user_id, tenant_id);

CREATE TABLE IF NOT EXISTS product.meego_connect_tickets (
    ticket_hash text PRIMARY KEY,
    plugin_id text NOT NULL,
    meego_tenant_key text NOT NULL,
    user_key text NOT NULL,
    meego_name text NOT NULL DEFAULT '',
    feishu_tenant_key text NOT NULL,
    expires_at timestamptz NOT NULL,
    csrf_hash text,
    browser_hash text,
    consumed_at timestamptz,
    binding_id uuid REFERENCES product.meego_bindings (id),
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'web';
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS meego_binding_id uuid REFERENCES product.meego_bindings (id);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'sessions_channel_known') THEN
        ALTER TABLE product.sessions ADD CONSTRAINT sessions_channel_known CHECK (
            (channel = 'web' AND meego_binding_id IS NULL) OR (channel = 'meego' AND meego_binding_id IS NOT NULL)
        );
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS sessions_meego_binding_idx ON product.sessions (meego_binding_id) WHERE meego_binding_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE ON product.meego_bindings, product.meego_connect_tickets TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0077_meego_host.sql') ON CONFLICT DO NOTHING;
COMMIT;
