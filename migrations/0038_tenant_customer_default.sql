ALTER TABLE product.tenants
    ADD COLUMN default_customer_preference text,
    ADD COLUMN customer_preference_revision integer NOT NULL DEFAULT 0,
    ADD CONSTRAINT tenant_default_customer_known CHECK (default_customer_preference IN ('UAES', '理想', '蔚来')),
    ADD CONSTRAINT tenant_customer_revision_nonnegative CHECK (customer_preference_revision >= 0);

INSERT INTO product.schema_migrations (version) VALUES ('0038_tenant_customer_default.sql') ON CONFLICT DO NOTHING;
