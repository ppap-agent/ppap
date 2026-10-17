-- Preserve historical aggregate rows; do not invent a file allocation for them.
ALTER TABLE product.usage_events ADD COLUMN IF NOT EXISTS file_version_id uuid;
ALTER TABLE product.usage_events DROP CONSTRAINT IF EXISTS usage_frozen_input;
ALTER TABLE product.usage_events ADD CONSTRAINT usage_frozen_input
    FOREIGN KEY (round_id, file_version_id) REFERENCES product.round_inputs(round_id, file_version_id);
ALTER TABLE product.usage_events DROP CONSTRAINT IF EXISTS usage_file_requires_round;
ALTER TABLE product.usage_events ADD CONSTRAINT usage_file_requires_round
    CHECK (file_version_id IS NULL OR round_id IS NOT NULL);
DROP INDEX IF EXISTS product.usage_events_round_slice_idx;
CREATE UNIQUE INDEX usage_events_round_slice_idx ON product.usage_events(round_id, is_free_trial)
    WHERE round_id IS NOT NULL AND file_version_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS usage_events_file_slice_idx
    ON product.usage_events(round_id, file_version_id, is_free_trial) WHERE file_version_id IS NOT NULL;
INSERT INTO product.schema_migrations (version) VALUES ('0027_file_usage.sql') ON CONFLICT DO NOTHING;
