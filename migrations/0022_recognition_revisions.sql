BEGIN;
ALTER TABLE product.audit_drafts
    ADD COLUMN IF NOT EXISTS input_revision integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS recognition_request_id uuid;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.audit_drafts'::regclass AND conname = 'task_revision_nonnegative') THEN
ALTER TABLE product.audit_drafts ADD CONSTRAINT task_revision_nonnegative CHECK (input_revision >= 0);
END IF;
END $$;

GRANT SELECT (id, tenant_id, status, input_revision, task_completion_reason) ON product.audit_drafts TO ppap_worker;
GRANT SELECT (draft_id, analysis_job_id, selection) ON product.draft_items TO ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0022_recognition_revisions.sql')
ON CONFLICT DO NOTHING;
COMMIT;
