BEGIN;

-- Each terminal optimization has one durable intent, including historical
-- Rounds without a recognition Task. Existing task/ZIP identities are unchanged.
DROP INDEX IF EXISTS engine.task_notification_intent;
CREATE UNIQUE INDEX task_notification_intent ON engine.jobs
    (tenant_id, (COALESCE(payload->>'task_id', '')), (payload->>'stage'),
     (COALESCE(payload->>'package_id', '')), (COALESCE(payload->>'operation_id', '')))
    WHERE kind = 'feishu_notification';
CREATE UNIQUE INDEX IF NOT EXISTS optimization_notification_intent ON engine.jobs
    (tenant_id, (payload->>'operation_id'))
    WHERE kind = 'feishu_notification' AND payload->>'stage' = 'optimization_completed';

INSERT INTO product.schema_migrations (version) VALUES ('0051_optimization_notifications.sql') ON CONFLICT DO NOTHING;
COMMIT;
