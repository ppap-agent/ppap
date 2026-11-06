BEGIN;

-- Acknowledgments belong to the member, not browser component lifetime. A new
-- immutable official revision has its own notice; another member is unaffected.
CREATE TABLE IF NOT EXISTS product.suite_official_update_views (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    user_id uuid NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    suite_key text NOT NULL,
    official_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, official_revision_id)
);

CREATE TABLE IF NOT EXISTS product.suite_upgrade_operations (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    operation_id uuid NOT NULL,
    suite_key text NOT NULL,
    source_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    target_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    revision_id uuid NOT NULL UNIQUE REFERENCES product.suite_revisions(id),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[a-f0-9]{64}$'),
    created_by uuid REFERENCES product.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id)
);

CREATE OR REPLACE FUNCTION product.validate_suite_official_update_view() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM product.memberships WHERE tenant_id=NEW.tenant_id AND user_id=NEW.user_id AND role='member')
       OR NOT EXISTS (SELECT 1 FROM product.suite_revisions WHERE id=NEW.official_revision_id AND tenant_id IS NULL AND suite_key=NEW.suite_key) THEN
        RAISE EXCEPTION 'suite_update_scope_mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS suite_official_update_view_scope ON product.suite_official_update_views;
CREATE TRIGGER suite_official_update_view_scope BEFORE INSERT ON product.suite_official_update_views
    FOR EACH ROW EXECUTE FUNCTION product.validate_suite_official_update_view();

CREATE OR REPLACE FUNCTION product.validate_suite_upgrade_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate product.suite_revisions%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF (to_jsonb(NEW)-'created_by') IS DISTINCT FROM (to_jsonb(OLD)-'created_by') OR NEW.created_by IS NOT NULL THEN
            RAISE EXCEPTION 'immutable_suite_upgrade_operation' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO candidate FROM product.suite_revisions WHERE id=NEW.revision_id;
    IF candidate.id IS NULL OR candidate.tenant_id IS DISTINCT FROM NEW.tenant_id OR candidate.suite_key<>NEW.suite_key
       OR candidate.base_revision_id IS DISTINCT FROM NEW.target_revision_id OR candidate.created_by IS DISTINCT FROM NEW.created_by
       OR NEW.created_by IS NULL
       OR NOT EXISTS (SELECT 1 FROM product.memberships WHERE tenant_id=NEW.tenant_id AND user_id=NEW.created_by AND role='member')
       OR NOT EXISTS (SELECT 1 FROM product.suite_revisions WHERE id=NEW.source_revision_id AND suite_key=NEW.suite_key AND (tenant_id IS NULL OR tenant_id=NEW.tenant_id))
       OR NOT EXISTS (SELECT 1 FROM product.suite_revisions WHERE id=NEW.target_revision_id AND suite_key=NEW.suite_key AND tenant_id IS NULL) THEN
        RAISE EXCEPTION 'suite_upgrade_scope_mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS suite_upgrade_operation_scope ON product.suite_upgrade_operations;
CREATE TRIGGER suite_upgrade_operation_scope BEFORE INSERT OR UPDATE ON product.suite_upgrade_operations
    FOR EACH ROW EXECUTE FUNCTION product.validate_suite_upgrade_operation();

REVOKE ALL ON product.suite_official_update_views, product.suite_upgrade_operations FROM ppap_api, ppap_worker;
GRANT SELECT, INSERT ON product.suite_official_update_views, product.suite_upgrade_operations TO ppap_api;
GRANT SELECT ON product.suite_upgrade_operations TO ppap_worker;
INSERT INTO product.schema_migrations(version) VALUES ('0053_suite_official_upgrades.sql') ON CONFLICT DO NOTHING;
COMMIT;
