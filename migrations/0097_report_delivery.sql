BEGIN;

-- A Feishu report document's delivery (#359): once the document is created, PPAP sets it read-only for others in the
-- enterprise and attaches the round's frozen report PDFs, each named at the top with its fingerprint. Each step's result
-- is kept beside the document's own status; a step that failed is retried by the person who delivered it, under a short
-- lease so two requests never attach the same PDF twice. `action_id` is the signing action that delivered it, whose
-- receipt shows these results. Rows from before keep NULL attachments. Every statement is safe to run again.
ALTER TABLE product.report_exports
    ADD COLUMN IF NOT EXISTS action_id uuid,
    ADD COLUMN IF NOT EXISTS read_only text,
    ADD COLUMN IF NOT EXISTS read_only_error text,
    ADD COLUMN IF NOT EXISTS attachments jsonb,
    ADD COLUMN IF NOT EXISTS lease uuid,
    ADD COLUMN IF NOT EXISTS lease_until timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.report_exports'::regclass AND conname = 'report_exports_action_fk') THEN
        ALTER TABLE product.report_exports ADD CONSTRAINT report_exports_action_fk FOREIGN KEY (action_id) REFERENCES product.actions (id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.report_exports'::regclass AND conname = 'report_exports_read_only_check') THEN
        ALTER TABLE product.report_exports ADD CONSTRAINT report_exports_read_only_check CHECK (read_only IN ('set', 'failed', 'unknown'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.report_exports'::regclass AND conname = 'report_exports_attachments_check') THEN
        ALTER TABLE product.report_exports ADD CONSTRAINT report_exports_attachments_check CHECK (attachments IS NULL OR jsonb_typeof(attachments) = 'array');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS report_exports_action ON product.report_exports (action_id) WHERE action_id IS NOT NULL;

INSERT INTO product.schema_migrations (version) VALUES ('0097_report_delivery.sql') ON CONFLICT DO NOTHING;
COMMIT;
