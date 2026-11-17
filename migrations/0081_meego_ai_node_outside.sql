BEGIN;

-- A PPAP AI node can be completed in Feishu Project itself, without PPAP's writeback or release
-- (the platform offers "确认并完成节点" after a rollback). PPAP cannot prevent that; it records
-- it, so the work item shows the node moved on without PPAP. Set from the completion event's
-- source, cleared when the flow comes back to the node.
ALTER TABLE product.meego_ai_nodes ADD COLUMN IF NOT EXISTS completed_outside boolean NOT NULL DEFAULT false;

INSERT INTO product.schema_migrations (version) VALUES ('0081_meego_ai_node_outside.sql') ON CONFLICT DO NOTHING;
COMMIT;
