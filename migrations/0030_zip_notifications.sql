BEGIN;
-- The runner replays every migration. Preserve the later operation scope so
-- replay cannot collapse two finished optimizations into one Task/ZIP intent.
DROP INDEX IF EXISTS engine.task_notification_intent;
CREATE UNIQUE INDEX task_notification_intent ON engine.jobs
    (tenant_id, (COALESCE(payload->>'task_id', '')), (payload->>'stage'),
     (COALESCE(payload->>'package_id', '')), (COALESCE(payload->>'operation_id', '')))
    WHERE kind = 'feishu_notification';
INSERT INTO product.schema_migrations (version) VALUES ('0030_zip_notifications.sql') ON CONFLICT DO NOTHING;
COMMIT;
