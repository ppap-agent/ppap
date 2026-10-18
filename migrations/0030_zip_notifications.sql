BEGIN;
DROP INDEX IF EXISTS engine.task_notification_intent;
CREATE UNIQUE INDEX task_notification_intent ON engine.jobs
    (tenant_id, (payload->>'task_id'), (payload->>'stage'), (COALESCE(payload->>'package_id', '')))
    WHERE kind = 'feishu_notification';
INSERT INTO product.schema_migrations (version) VALUES ('0030_zip_notifications.sql') ON CONFLICT DO NOTHING;
COMMIT;
