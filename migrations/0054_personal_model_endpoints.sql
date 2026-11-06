-- Metadata selects one exact personal provider; authenticated ciphertext binds
-- these same fields to the owner. Existing credentials retain their destination.
ALTER TABLE product.personal_model_credentials DROP CONSTRAINT IF EXISTS personal_model_credentials_provider_check;
ALTER TABLE product.personal_model_credentials DROP CONSTRAINT IF EXISTS personal_model_credentials_model_check;
ALTER TABLE product.personal_model_credentials ADD COLUMN IF NOT EXISTS base_url text NOT NULL DEFAULT 'https://api.deepseek.com';
ALTER TABLE product.personal_model_credentials ADD COLUMN IF NOT EXISTS revision uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE product.personal_model_credentials DROP CONSTRAINT IF EXISTS personal_model_credentials_provider_supported;
ALTER TABLE product.personal_model_credentials ADD CONSTRAINT personal_model_credentials_provider_supported CHECK (provider IN ('deepseek','responses-compatible'));
ALTER TABLE product.personal_model_credentials DROP CONSTRAINT IF EXISTS personal_model_credentials_model_bounded;
ALTER TABLE product.personal_model_credentials ADD CONSTRAINT personal_model_credentials_model_bounded CHECK (length(model) BETWEEN 1 AND 128);
ALTER TABLE product.personal_model_credentials DROP CONSTRAINT IF EXISTS personal_model_credentials_endpoint_bounded;
ALTER TABLE product.personal_model_credentials ADD CONSTRAINT personal_model_credentials_endpoint_bounded CHECK (length(base_url) BETWEEN 1 AND 2048);
INSERT INTO product.schema_migrations (version) VALUES ('0054_personal_model_endpoints.sql') ON CONFLICT DO NOTHING;
