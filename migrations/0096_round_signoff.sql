BEGIN;

-- Signing off a round (#357): the person who ran it (the round's creator) stands by its conclusion. Who signed or
-- withdrew it, when and why are their actions in product.actions (#362); the round keeps only the time it was signed,
-- the signing action and what was signed (each file's conclusion, the review version of every reviewed item, the items
-- accepted on the machine's conclusion, the reports' fingerprint). Withdrawing clears all three; a re-audit is a new
-- round, signed on its own. Every statement is safe to run again.
ALTER TABLE product.audit_rounds
    ADD COLUMN IF NOT EXISTS signed_at timestamptz,
    ADD COLUMN IF NOT EXISTS signoff jsonb,
    ADD COLUMN IF NOT EXISTS signoff_action_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.audit_rounds'::regclass AND conname = 'audit_rounds_signoff_action_fk') THEN
        ALTER TABLE product.audit_rounds ADD CONSTRAINT audit_rounds_signoff_action_fk FOREIGN KEY (signoff_action_id) REFERENCES product.actions (id);
    END IF;
    -- Signed means all of it: when, by which action, and what; unsigned means none of it.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.audit_rounds'::regclass AND conname = 'audit_rounds_signoff_whole') THEN
        ALTER TABLE product.audit_rounds ADD CONSTRAINT audit_rounds_signoff_whole CHECK (
            (signed_at IS NULL) = (signoff IS NULL)
            AND (signed_at IS NULL) = (signoff_action_id IS NULL)
            AND (signoff IS NULL OR jsonb_typeof(signoff) = 'object')
        );
    END IF;
END $$;

GRANT UPDATE (signed_at, signoff, signoff_action_id) ON product.audit_rounds TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0096_round_signoff.sql') ON CONFLICT DO NOTHING;
COMMIT;
