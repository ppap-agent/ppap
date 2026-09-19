ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS access_token text;
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS refresh_token text;
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS access_expires_at timestamptz;
ALTER TABLE product.sessions ADD COLUMN IF NOT EXISTS refresh_expires_at timestamptz;
