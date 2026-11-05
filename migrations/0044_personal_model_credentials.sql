-- Ciphertext only. Membership/account removal revokes personal model access;
-- shared Checker artifacts never reference or cascade from this table.
CREATE TABLE product.personal_model_credentials (
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    provider text NOT NULL DEFAULT 'deepseek' CHECK (provider = 'deepseek'),
    model text NOT NULL DEFAULT 'deepseek-flash' CHECK (model = 'deepseek-flash'),
    key_ciphertext text NOT NULL CHECK (length(key_ciphertext) > 0),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, user_id),
    FOREIGN KEY (user_id, tenant_id) REFERENCES product.memberships (user_id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES product.users (id) ON DELETE CASCADE
);

GRANT SELECT, INSERT, UPDATE, DELETE ON product.personal_model_credentials TO ppap_api;
GRANT SELECT ON product.personal_model_credentials TO ppap_worker;
-- Needed to verify current membership before any personal model request.
GRANT SELECT ON product.memberships TO ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0044_personal_model_credentials.sql') ON CONFLICT DO NOTHING;
