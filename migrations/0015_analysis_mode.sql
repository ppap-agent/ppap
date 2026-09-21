ALTER TABLE product.audit_drafts
    ADD COLUMN IF NOT EXISTS analysis_mode text NOT NULL DEFAULT 'standard';
ALTER TABLE product.audit_rounds
    ADD COLUMN IF NOT EXISTS analysis_mode text NOT NULL DEFAULT 'standard';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'draft_analysis_mode_known' AND conrelid = 'product.audit_drafts'::regclass
    ) THEN
        ALTER TABLE product.audit_drafts
            ADD CONSTRAINT draft_analysis_mode_known CHECK (analysis_mode IN ('standard', 'deep'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'round_analysis_mode_known' AND conrelid = 'product.audit_rounds'::regclass
    ) THEN
        ALTER TABLE product.audit_rounds
            ADD CONSTRAINT round_analysis_mode_known CHECK (analysis_mode IN ('standard', 'deep'));
    END IF;
END $$;

INSERT INTO product.schema_migrations (version) VALUES ('0015_analysis_mode.sql')
ON CONFLICT DO NOTHING;
