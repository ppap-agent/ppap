BEGIN;

-- Saving a rule version reads every clause at once as one batch.
-- When the batch has no reading left in flight, its initiator gets one Feishu notification.
ALTER TABLE product.rule_formalization_operations ADD COLUMN IF NOT EXISTS batch_id uuid;
CREATE INDEX IF NOT EXISTS rule_formalization_operations_batch
    ON product.rule_formalization_operations (tenant_id, batch_id) WHERE batch_id IS NOT NULL;

INSERT INTO product.schema_migrations (version) VALUES ('0069_formalization_batches.sql') ON CONFLICT DO NOTHING;
COMMIT;
