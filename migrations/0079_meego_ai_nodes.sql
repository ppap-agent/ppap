BEGIN;

-- Feishu Project AI nodes running the PPAP app, one row per node of a work item.
-- Kept current from the app's signed lifecycle events and from PPAP's own
-- completion of the node. It holds no audit data: the audit is the work item's
-- ordinary task and round, started and confirmed by a person in the plugin.
CREATE TABLE IF NOT EXISTS product.meego_ai_nodes (
    project_key text NOT NULL,
    work_item_id bigint NOT NULL,
    state_key text NOT NULL,
    work_item_type_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('idle', 'running', 'done', 'stopped', 'completed', 'removed')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_key, work_item_id, state_key)
);

GRANT SELECT, INSERT, UPDATE ON product.meego_ai_nodes TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0079_meego_ai_nodes.sql') ON CONFLICT DO NOTHING;
COMMIT;
