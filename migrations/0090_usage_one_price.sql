BEGIN;

-- Every target file is one 500-point charge at acceptance. The historical 250-point 'deep_surcharge' was never
-- recorded in any deployment, so the charge kind goes; a database that holds one stops here instead of losing it.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'product' AND table_name = 'usage_events' AND column_name = 'charge')
       AND EXISTS (SELECT 1 FROM product.usage_events WHERE charge <> 'audit') THEN
        RAISE EXCEPTION 'usage_events holds a charge other than audit; decide how to keep it before dropping the column';
    END IF;
END $$;

DROP INDEX IF EXISTS product.usage_events_file_slice_idx;
ALTER TABLE product.usage_events DROP CONSTRAINT IF EXISTS usage_charge_known;
ALTER TABLE product.usage_events DROP COLUMN IF EXISTS charge;
CREATE UNIQUE INDEX usage_events_file_slice_idx
    ON product.usage_events(round_id, file_version_id, is_free_trial) WHERE file_version_id IS NOT NULL;

-- Only acceptance (the API) records usage; the worker never writes it.
REVOKE INSERT ON product.usage_events FROM ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0090_usage_one_price.sql') ON CONFLICT DO NOTHING;
COMMIT;
