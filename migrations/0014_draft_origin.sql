-- Keep a re-audit draft linked when resumed from the draft list.
ALTER TABLE product.audit_drafts
    ADD COLUMN IF NOT EXISTS predecessor_id uuid REFERENCES product.audit_rounds (id);
INSERT INTO product.schema_migrations (version) VALUES ('0014_draft_origin.sql') ON CONFLICT DO NOTHING;
