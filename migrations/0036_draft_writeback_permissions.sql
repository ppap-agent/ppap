-- Draft targets are replaceable. Frozen round targets are not.
-- _replace_draft_target deletes the prior draft selection in its transaction.
GRANT DELETE ON product.draft_writeback_targets TO ppap_api;

INSERT INTO product.schema_migrations (version)
VALUES ('0036_draft_writeback_permissions.sql') ON CONFLICT DO NOTHING;
