BEGIN;

-- The Feishu Docs add-on runs in a frame inside Feishu, where the browser sends
-- no cookie of ours. Its sessions are ordinary product.sessions rows with
-- channel 'docs', presented as Bearer tokens and never accepted as cookies.
ALTER TABLE product.sessions DROP CONSTRAINT IF EXISTS sessions_channel_known;
ALTER TABLE product.sessions ADD CONSTRAINT sessions_channel_known CHECK (
    (channel IN ('web', 'docs') AND meego_binding_id IS NULL) OR (channel = 'meego' AND meego_binding_id IS NOT NULL)
);

INSERT INTO product.schema_migrations (version) VALUES ('0078_docs_host_session.sql') ON CONFLICT DO NOTHING;
COMMIT;
