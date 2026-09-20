ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS access_token text;
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS refresh_token text;
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS access_expires_at timestamptz;
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS refresh_expires_at timestamptz;

INSERT INTO product.schema_migrations (version) VALUES ('0004_session_oauth.sql')
ON CONFLICT DO NOTHING;
