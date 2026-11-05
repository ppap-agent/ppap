BEGIN;

-- Immutable save receipts are separate from adopting a tenant default. The
-- operation and candidate are committed together so a lost response is readable.
CREATE TABLE IF NOT EXISTS product.suite_candidate_operations (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    operation_id uuid NOT NULL,
    base_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[a-f0-9]{64}$'),
    created_by uuid REFERENCES product.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id),
    UNIQUE (revision_id)
);

CREATE OR REPLACE FUNCTION product.validate_suite_candidate_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate product.suite_revisions%ROWTYPE;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - 'created_by') IS DISTINCT FROM (to_jsonb(OLD) - 'created_by') OR NEW.created_by IS NOT NULL THEN
            RAISE EXCEPTION 'immutable_suite_candidate_operation' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO candidate FROM product.suite_revisions WHERE id = NEW.revision_id;
    IF candidate.id IS NULL OR candidate.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR candidate.base_revision_id IS DISTINCT FROM NEW.base_revision_id
       OR candidate.created_by IS DISTINCT FROM NEW.created_by OR NEW.created_by IS NULL
       OR NOT EXISTS (SELECT 1 FROM product.memberships WHERE tenant_id = NEW.tenant_id AND user_id = NEW.created_by) THEN
        RAISE EXCEPTION 'suite_candidate_scope_mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS suite_candidate_operation_scope ON product.suite_candidate_operations;
CREATE TRIGGER suite_candidate_operation_scope BEFORE INSERT OR UPDATE ON product.suite_candidate_operations
    FOR EACH ROW EXECUTE FUNCTION product.validate_suite_candidate_operation();

GRANT SELECT, INSERT ON product.suite_candidate_operations TO ppap_api;
GRANT SELECT ON product.suite_candidate_operations TO ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0047_checker_candidate_operations.sql') ON CONFLICT DO NOTHING;
COMMIT;
