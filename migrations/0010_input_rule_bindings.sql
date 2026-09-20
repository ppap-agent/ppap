ALTER TABLE product.draft_items ADD COLUMN IF NOT EXISTS rule_version_id uuid REFERENCES product.rule_versions (id);
ALTER TABLE product.round_inputs
    ADD COLUMN IF NOT EXISTS rule_version_id uuid REFERENCES product.rule_versions (id),
    ADD COLUMN IF NOT EXISTS rule_title text,
    ADD COLUMN IF NOT EXISTS rule_body jsonb;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'product' AND table_name = 'audit_drafts' AND column_name = 'rule_version_id') THEN
        UPDATE product.draft_items i SET rule_version_id = d.rule_version_id
        FROM product.audit_drafts d WHERE d.id = i.draft_id AND i.role = 'target';
        ALTER TABLE product.audit_drafts DROP COLUMN rule_version_id;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'product' AND table_name = 'audit_rounds' AND column_name = 'rule_version_id') THEN
        -- Preserve the accepted snapshot, not the current rule_versions body.
        UPDATE product.round_inputs i
        SET rule_version_id = r.rule_version_id, rule_title = r.rule_title, rule_body = r.rule_body
        FROM product.audit_rounds r WHERE r.id = i.round_id AND i.role = 'target';
        UPDATE product.audit_rounds r SET request_fingerprint = (
            SELECT encode(sha256(convert_to(COALESCE(string_agg(
                i.file_version_id::text || ':' || i.role || ':' || COALESCE(i.rule_version_id::text, ''),
                ';' ORDER BY i.file_version_id::text, i.role, i.rule_version_id::text
            ), ''), 'UTF8')), 'hex') FROM product.round_inputs i WHERE i.round_id = r.id
        );
        ALTER TABLE product.audit_rounds DROP COLUMN rule_version_id, DROP COLUMN rule_title, DROP COLUMN rule_body;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'draft_reference_has_no_rule' AND conrelid = 'product.draft_items'::regclass) THEN
        ALTER TABLE product.draft_items ADD CONSTRAINT draft_reference_has_no_rule CHECK (role = 'target' OR rule_version_id IS NULL);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'round_input_rule_binding' AND conrelid = 'product.round_inputs'::regclass) THEN
        ALTER TABLE product.round_inputs ADD CONSTRAINT round_input_rule_binding CHECK (
            (role = 'target' AND rule_version_id IS NOT NULL AND rule_title IS NOT NULL AND rule_body IS NOT NULL)
            OR (role = 'reference' AND rule_version_id IS NULL AND rule_title IS NULL AND rule_body IS NULL)
        );
    END IF;
END $$;

INSERT INTO product.schema_migrations (version) VALUES ('0010_input_rule_bindings.sql') ON CONFLICT DO NOTHING;
