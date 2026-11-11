BEGIN;

-- A tenant may use its own model key (tenant BYOK); without one it uses the platform models.
-- Ciphertext only: the key is encrypted with its tenant, destination and models, so a copied
-- or edited row cannot redirect it. Operators set it; customers have no API for it.
CREATE TABLE IF NOT EXISTS product.tenant_model_credentials (
    tenant_id uuid PRIMARY KEY REFERENCES product.tenants(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider = 'responses-compatible'),
    base_url text NOT NULL CHECK (char_length(base_url) BETWEEN 9 AND 2048 AND base_url LIKE 'https://%'),
    text_model text NOT NULL CHECK (char_length(text_model) BETWEEN 1 AND 128),
    vision_model text NOT NULL CHECK (char_length(vision_model) BETWEEN 1 AND 128),
    coding_model text NOT NULL CHECK (char_length(coding_model) BETWEEN 1 AND 128),
    key_ciphertext text NOT NULL CHECK (length(key_ciphertext) > 0),
    revision uuid NOT NULL,
    updated_by text NOT NULL CHECK (char_length(updated_by) BETWEEN 1 AND 200),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON product.tenant_model_credentials TO ppap_api;
GRANT SELECT ON product.tenant_model_credentials TO ppap_worker;

-- Personal keys are retired. Operations that froze a personal identity keep that identity
-- in their own rows and stay readable; nothing referenced this table.
DROP TABLE IF EXISTS product.personal_model_credentials;

INSERT INTO product.schema_migrations (version) VALUES ('0062_tenant_model_credentials.sql') ON CONFLICT DO NOTHING;
COMMIT;
