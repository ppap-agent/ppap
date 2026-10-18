-- Actual provider debit evidence is distinct from a usage-report acknowledgement.
-- Do not backfill from external_confirmed_at or change the accepted audit round.
ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS debit_order_id text;
ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS debited_at timestamptz;
ALTER TABLE product.usage_events DROP CONSTRAINT IF EXISTS usage_debit_requires_order;
ALTER TABLE product.usage_events ADD CONSTRAINT usage_debit_requires_order CHECK (
    (debit_order_id IS NULL AND debited_at IS NULL) OR
    (debit_order_id IS NOT NULL AND length(btrim(debit_order_id)) > 0 AND debited_at IS NOT NULL AND NOT is_free_trial)
);
INSERT INTO product.schema_migrations (version) VALUES ('0032_usage_debit.sql') ON CONFLICT DO NOTHING;
