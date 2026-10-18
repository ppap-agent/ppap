BEGIN;
ALTER TABLE product.audit_drafts ADD COLUMN IF NOT EXISTS notification_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE product.audit_drafts ADD COLUMN IF NOT EXISTS notification_revision bigint NOT NULL DEFAULT 0 CHECK (notification_revision >= 0);
CREATE UNIQUE INDEX IF NOT EXISTS task_notification_intent ON engine.jobs
    (tenant_id, (payload->>'task_id'), (payload->>'stage')) WHERE kind = 'feishu_notification';
GRANT UPDATE (attempt) ON engine.jobs TO ppap_api;
GRANT INSERT ON engine.attempts TO ppap_api;
GRANT UPDATE (finished_at, outcome, diagnostic) ON engine.attempts TO ppap_api;
INSERT INTO product.schema_migrations (version) VALUES ('0029_task_notifications.sql') ON CONFLICT DO NOTHING;
COMMIT;
