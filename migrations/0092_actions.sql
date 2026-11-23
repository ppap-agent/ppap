BEGIN;

-- What people did, and only that: every write request made by a known person, through any channel (#362).
-- A row is written before the request takes effect and finished with its result; one cut off in between stays
-- 'pending' and is read as unknown. Who, when, how and with what are kept here and nowhere else.
CREATE TABLE IF NOT EXISTS product.actions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    user_id uuid NOT NULL REFERENCES product.users(id),
    at timestamptz NOT NULL DEFAULT now(),
    -- web, docs (Docs add-on), meego (Meego plugin), mcp (an MCP client), feishu_card
    channel text NOT NULL CHECK (channel IN ('web', 'docs', 'meego', 'mcp', 'feishu_card')),
    grant_id uuid,
    intent_id uuid,
    method text NOT NULL CHECK (method IN ('POST', 'PUT', 'PATCH', 'DELETE')),
    route text NOT NULL CHECK (length(route) BETWEEN 1 AND 300),
    path text NOT NULL CHECK (length(path) BETWEEN 1 AND 2000),
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    outcome text NOT NULL DEFAULT 'pending' CHECK (outcome IN ('pending', 'succeeded', 'failed', 'unknown')),
    status_code integer,
    error text CHECK (error IS NULL OR length(error) <= 200),
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS actions_user_idx ON product.actions (tenant_id, user_id, at DESC);
CREATE INDEX IF NOT EXISTS actions_intent_idx ON product.actions (intent_id) WHERE intent_id IS NOT NULL;

GRANT SELECT, INSERT ON product.actions TO ppap_api;
-- A row is only ever finished, never rewritten.
GRANT UPDATE (outcome, status_code, error, finished_at) ON product.actions TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0092_actions.sql') ON CONFLICT DO NOTHING;
COMMIT;
