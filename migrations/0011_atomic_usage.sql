ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS round_id uuid REFERENCES product.audit_rounds(id);
ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS report_claim uuid;
ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS reporting_at timestamptz;
ALTER TABLE product.usage_events DROP CONSTRAINT IF EXISTS usage_external_status_known;
-- Old failed reports did not distinguish a lost response from a rejected request.
UPDATE product.usage_events SET external_status = 'unknown' WHERE external_status = 'failed';
ALTER TABLE product.usage_events ADD CONSTRAINT usage_external_status_known
    CHECK (external_status IN ('none', 'pending', 'reporting', 'confirmed', 'unknown'));
CREATE UNIQUE INDEX IF NOT EXISTS usage_events_round_slice_idx
    ON product.usage_events(round_id, is_free_trial) WHERE round_id IS NOT NULL;
INSERT INTO product.schema_migrations(version) VALUES ('0011_atomic_usage.sql') ON CONFLICT DO NOTHING;
