BEGIN;

-- Signing is the person's own act in the Web; an MCP client only proposes it (#348). Who did it and when is in
-- product.actions (#362) and nowhere else, so the columns that duplicated it go, after their facts are carried over.
-- Every statement is safe to run again: the carry-over reads only columns that still exist.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'product' AND table_name = 'mcp_operations' AND column_name = 'confirmed_by') THEN
        -- A confirmation the person approved on the old page and that then ran: that call was their action.
        INSERT INTO product.actions (id, tenant_id, user_id, at, channel, grant_id, method, route, path, params, outcome, status_code, error, finished_at)
        SELECT gen_random_uuid(), g.tenant_id, o.confirmed_by, o.confirmed_at, 'mcp', o.grant_id, o.arguments->>'method',
               left(o.arguments->>'path', 300), left(o.arguments->>'path', 2000), COALESCE(NULLIF(o.arguments->'body', 'null'::jsonb), '{}'::jsonb),
               o.state, (o.result->>'http_status')::integer, left(o.result->'data'->>'error', 200), o.updated_at
        FROM product.mcp_operations o JOIN product.mcp_grants g ON g.id = o.grant_id
        WHERE o.confirmed_by IS NOT NULL AND o.state IN ('succeeded', 'failed', 'unknown');
        -- A refusal on the old page: a decline, with the reason the person gave.
        INSERT INTO product.actions (id, tenant_id, user_id, at, channel, grant_id, method, route, path, params, outcome, status_code, finished_at)
        SELECT gen_random_uuid(), g.tenant_id, o.confirmed_by, o.confirmed_at, 'web', o.grant_id, 'POST', '/intents/{intent_id}/decline',
               '/intents/' || o.id || '/decline', jsonb_strip_nulls(jsonb_build_object('reason', o.result->>'denied_reason')), 'succeeded', 200, o.confirmed_at
        FROM product.mcp_operations o JOIN product.mcp_grants g ON g.id = o.grant_id
        WHERE o.confirmed_by IS NOT NULL AND o.state = 'denied';
        -- Confirmations the old page left open or refused end there; a client reading one is told to propose anew.
        UPDATE product.mcp_operations
        SET state = 'failed', updated_at = now(),
            result = jsonb_build_object('data', jsonb_build_object('error', CASE WHEN state = 'denied' THEN 'denied_by_user' ELSE 'confirmation_retired' END), 'http_status', 410, 'probes', '[]'::jsonb)
        WHERE confirmation IS NOT NULL AND state IN ('confirmation_required', 'ready', 'denied');
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'product' AND table_name = 'rulebook_revisions' AND column_name = 'clarified_by') THEN
        INSERT INTO product.actions (id, tenant_id, user_id, at, channel, method, route, path, params, outcome, status_code, finished_at)
        SELECT gen_random_uuid(), r.tenant_id, r.clarified_by, r.clarified_at, 'mcp', 'POST', '/rulebooks/{customer_id}/{version}/{revision}/clarified',
               '/rulebooks/' || r.customer_id || '/' || r.version || '/' || r.revision || '/clarified', '{}'::jsonb, 'succeeded', 200, r.clarified_at
        FROM product.rulebook_revisions r WHERE r.clarified_by IS NOT NULL
        -- One the old page approved is carried over above already.
        AND NOT EXISTS (SELECT 1 FROM product.actions a WHERE a.user_id = r.clarified_by AND a.path = '/rulebooks/' || r.customer_id || '/' || r.version || '/' || r.revision || '/clarified');
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'product' AND table_name = 'rule_clause_readings' AND column_name = 'confirmed_by') THEN
        -- Readings published together were one call.
        INSERT INTO product.actions (id, tenant_id, user_id, at, channel, method, route, path, params, outcome, status_code, finished_at)
        SELECT gen_random_uuid(), r.tenant_id, c.confirmed_by, c.confirmed_at, 'mcp', 'PUT', '/rulebooks/{customer_id}/{version}/{revision}/readings',
               '/rulebooks/' || r.customer_id || '/' || r.version || '/' || r.revision || '/readings',
               jsonb_build_object('readings', jsonb_object_agg(c.clause_id, c.reading)), 'succeeded', 200, c.confirmed_at
        FROM product.rule_clause_readings c JOIN product.rulebook_revisions r ON r.id = c.revision_id
        WHERE NOT EXISTS (SELECT 1 FROM product.actions a WHERE a.user_id = c.confirmed_by AND a.method = 'PUT'
                          AND a.path = '/rulebooks/' || r.customer_id || '/' || r.version || '/' || r.revision || '/readings')
        GROUP BY r.tenant_id, r.customer_id, r.version, r.revision, c.confirmed_by, c.confirmed_at;
    END IF;
END $$;

ALTER TABLE product.mcp_operations
    DROP COLUMN IF EXISTS confirmation,
    DROP COLUMN IF EXISTS confirmed_by,
    DROP COLUMN IF EXISTS confirmed_at,
    DROP COLUMN IF EXISTS csrf_hash,
    DROP COLUMN IF EXISTS browser_hash;
-- 'proposed': a signing action the client proposed; what became of it is the person's action that names it.
ALTER TABLE product.mcp_operations DROP CONSTRAINT IF EXISTS mcp_operations_state_check;
ALTER TABLE product.mcp_operations ADD CONSTRAINT mcp_operations_state_check
    CHECK (state IN ('proposed', 'ready', 'running', 'succeeded', 'failed', 'unknown'));

ALTER TABLE product.rulebook_revisions DROP COLUMN IF EXISTS clarified_by;

-- A card no longer starts an audit (#355): clicks still waiting accepted nothing; the person starts it on the page.
UPDATE engine.jobs SET status = 'failed', lease_until = NULL, updated_at = now(),
    result = jsonb_build_object('state', 'rejected', 'reason', '请打开页面核对后开始审核。')
WHERE kind = 'task_confirmation' AND status IN ('queued', 'leased');
ALTER TABLE product.rule_clause_readings DROP COLUMN IF EXISTS confirmed_by, DROP COLUMN IF EXISTS confirmed_at;

INSERT INTO product.schema_migrations (version) VALUES ('0093_signing_in_the_web.sql') ON CONFLICT DO NOTHING;
COMMIT;
