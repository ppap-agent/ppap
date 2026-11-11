BEGIN;

-- Display corrections may also carry the project name. Receipts saved before
-- this change keep their four keys and stay valid; nothing is rewritten.
ALTER TABLE product.audit_display_requests DROP CONSTRAINT IF EXISTS audit_display_requests_display_values_check;
ALTER TABLE product.audit_display_requests DROP CONSTRAINT IF EXISTS audit_display_values_shape;
ALTER TABLE product.audit_display_requests ADD CONSTRAINT audit_display_values_shape CHECK (
    jsonb_typeof(display_values) = 'object'
    AND display_values ?& ARRAY['customer', 'model', 'part_number', 'part']
    AND display_values - ARRAY['customer', 'model', 'project', 'part_number', 'part'] = '{}'::jsonb
    AND jsonb_typeof(display_values->'customer') IN ('string', 'null')
    AND jsonb_typeof(display_values->'model') IN ('string', 'null')
    AND jsonb_typeof(display_values->'part_number') IN ('string', 'null')
    AND jsonb_typeof(display_values->'part') IN ('string', 'null')
    AND (NOT display_values ? 'project' OR jsonb_typeof(display_values->'project') IN ('string', 'null'))
);

INSERT INTO product.schema_migrations (version) VALUES ('0063_display_project.sql') ON CONFLICT DO NOTHING;
COMMIT;
