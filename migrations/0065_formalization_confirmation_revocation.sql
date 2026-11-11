BEGIN;

-- A confirmation can be withdrawn. The row stays as history; who withdrew it and when are kept.
ALTER TABLE product.rule_formalization_confirmations ADD COLUMN IF NOT EXISTS revoked_by uuid REFERENCES product.users(id);
ALTER TABLE product.rule_formalization_confirmations ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE product.rule_formalization_confirmations DROP CONSTRAINT IF EXISTS rule_formalization_confirmations_revocation;
ALTER TABLE product.rule_formalization_confirmations ADD CONSTRAINT rule_formalization_confirmations_revocation CHECK (
    (revoked_at IS NULL AND revoked_by IS NULL) OR (revoked_at IS NOT NULL AND NOT current)
);

INSERT INTO product.schema_migrations (version) VALUES ('0065_formalization_confirmation_revocation.sql') ON CONFLICT DO NOTHING;
COMMIT;
