BEGIN;
ALTER TABLE product.tenants
    ADD COLUMN IF NOT EXISTS default_customer_preference text,
    ADD COLUMN IF NOT EXISTS customer_preference_revision integer NOT NULL DEFAULT 0;

DO $$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.tenants'::regclass AND conname = 'tenant_default_customer_known') THEN
    ALTER TABLE product.tenants ADD CONSTRAINT tenant_default_customer_known
        CHECK (default_customer_preference IN ('UAES', '理想', '蔚来'));
END IF;
IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'product.tenants'::regclass AND conname = 'tenant_customer_revision_nonnegative') THEN
    ALTER TABLE product.tenants ADD CONSTRAINT tenant_customer_revision_nonnegative CHECK (customer_preference_revision >= 0);
END IF;
END $$;

INSERT INTO product.schema_migrations (version) VALUES ('0038_tenant_customer_default.sql') ON CONFLICT DO NOTHING;
COMMIT;
