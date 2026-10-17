-- Time PPAP observed a successful external receipt, not the provider's debit time.
-- Historical confirmations have no reliable observation timestamp; leave NULL.
ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS external_confirmed_at timestamptz;
ALTER TABLE product.usage_events DROP CONSTRAINT IF EXISTS usage_confirmation_requires_receipt;
ALTER TABLE product.usage_events ADD CONSTRAINT usage_confirmation_requires_receipt
    CHECK (external_confirmed_at IS NULL OR
           (external_status = 'confirmed' AND external_record_id IS NOT NULL AND NOT is_free_trial));
INSERT INTO product.schema_migrations (version) VALUES ('0028_usage_confirmation.sql') ON CONFLICT DO NOTHING;
