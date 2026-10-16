-- A recognition Task is the existing private audit draft, not a second ledger.
BEGIN;
ALTER TABLE product.audit_drafts
    ADD COLUMN IF NOT EXISTS task_fingerprint text,
    ADD COLUMN IF NOT EXISTS task_day date,
    ADD COLUMN IF NOT EXISTS task_sequence integer,
    ADD COLUMN IF NOT EXISTS customer_preference text,
    ADD COLUMN IF NOT EXISTS task_completion_reason text;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.audit_drafts'::regclass AND conname = 'task_number_complete') THEN
ALTER TABLE product.audit_drafts
    ADD CONSTRAINT task_number_complete CHECK (
        (task_fingerprint IS NULL AND task_day IS NULL AND task_sequence IS NULL)
        OR (task_fingerprint IS NOT NULL AND task_day IS NOT NULL AND task_sequence IS NOT NULL AND task_sequence > 0)
    ),
    ADD CONSTRAINT task_number_unique UNIQUE (tenant_id, task_day, task_sequence),
    ADD CONSTRAINT task_completion_known CHECK (task_completion_reason IS NULL OR task_completion_reason = 'no_matching_rules');
END IF;
END $$;

ALTER TABLE product.draft_items
    ADD COLUMN IF NOT EXISTS selection text NOT NULL DEFAULT 'selected',
    ADD COLUMN IF NOT EXISTS removal_reason text,
    ADD COLUMN IF NOT EXISTS validation_job_id uuid REFERENCES engine.jobs (id),
    ADD COLUMN IF NOT EXISTS analysis_job_id uuid REFERENCES engine.jobs (id),
    ADD COLUMN IF NOT EXISTS position integer NOT NULL DEFAULT 0;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.draft_items'::regclass AND conname = 'task_selection_known') THEN
ALTER TABLE product.draft_items
    ADD CONSTRAINT task_selection_known CHECK (selection IN ('selected', 'unselected', 'removed')),
    ADD CONSTRAINT task_removed_reason CHECK ((selection = 'removed') = (removal_reason IS NOT NULL));
END IF;
END $$;

INSERT INTO product.schema_migrations (version) VALUES ('0021_recognition_tasks.sql')
ON CONFLICT DO NOTHING;
COMMIT;
