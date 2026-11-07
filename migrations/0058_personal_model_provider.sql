-- Personal models are any approved Responses-compatible endpoint: no vendor default.
UPDATE product.personal_model_credentials SET provider = 'responses-compatible' WHERE provider <> 'responses-compatible';
ALTER TABLE product.personal_model_credentials ALTER COLUMN provider DROP DEFAULT;
ALTER TABLE product.personal_model_credentials ALTER COLUMN model DROP DEFAULT;
ALTER TABLE product.personal_model_credentials ALTER COLUMN base_url DROP DEFAULT;
ALTER TABLE product.personal_model_credentials DROP CONSTRAINT IF EXISTS personal_model_credentials_provider_supported;
ALTER TABLE product.personal_model_credentials ADD CONSTRAINT personal_model_credentials_provider_supported CHECK (provider = 'responses-compatible');
INSERT INTO product.schema_migrations (version) VALUES ('0058_personal_model_provider.sql') ON CONFLICT DO NOTHING;
