BEGIN;
ALTER TABLE product.file_versions ADD COLUMN IF NOT EXISTS upload_request jsonb;
INSERT INTO product.schema_migrations (version) VALUES ('0023_upload_requests.sql')
ON CONFLICT DO NOTHING;
COMMIT;
