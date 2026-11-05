BEGIN;

CREATE TABLE IF NOT EXISTS product.suite_objects (
    owner_scope text NOT NULL,
    digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    object_key text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_scope, digest),
    CHECK (object_key = 'suites/' || owner_scope || '/objects/' || digest)
);

CREATE TABLE IF NOT EXISTS product.suite_revisions (
    id uuid PRIMARY KEY,
    tenant_id uuid REFERENCES product.tenants(id),
    revision_number bigint NOT NULL CHECK (revision_number > 0),
    suite_key text NOT NULL CHECK (suite_key <> '' AND suite_key = btrim(suite_key)),
    related_suite_keys text[] NOT NULL DEFAULT '{}',
    base_revision_id uuid REFERENCES product.suite_revisions(id),
    overrides jsonb NOT NULL CHECK (jsonb_typeof(overrides) = 'object'),
    resolved_digest text NOT NULL CHECK (resolved_digest ~ '^[a-f0-9]{64}$'),
    created_by uuid REFERENCES product.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (base_revision_id IS DISTINCT FROM id)
);
CREATE UNIQUE INDEX IF NOT EXISTS suite_revision_number_tenant ON product.suite_revisions(tenant_id, suite_key, revision_number) WHERE tenant_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS suite_revision_number_official ON product.suite_revisions(suite_key, revision_number) WHERE tenant_id IS NULL;
CREATE INDEX IF NOT EXISTS suite_revisions_tenant_key ON product.suite_revisions(tenant_id, suite_key, created_at);

CREATE OR REPLACE FUNCTION product.check_suite_object() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.owner_scope = 'official' THEN
        IF current_user <> 'ppap_migrator' THEN RAISE EXCEPTION 'official_suite_write_forbidden'; END IF;
    ELSIF NOT EXISTS (SELECT 1 FROM product.tenants WHERE id::text = NEW.owner_scope) THEN
        RAISE EXCEPTION 'suite_object_tenant_missing';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS check_suite_object ON product.suite_objects;
CREATE TRIGGER check_suite_object BEFORE INSERT ON product.suite_objects FOR EACH ROW EXECUTE FUNCTION product.check_suite_object();

CREATE OR REPLACE FUNCTION product.check_suite_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent product.suite_revisions; resource record;
BEGIN
    IF NEW.tenant_id IS NULL AND current_user <> 'ppap_migrator' THEN RAISE EXCEPTION 'official_suite_write_forbidden'; END IF;
    IF NEW.tenant_id IS NOT NULL AND NEW.created_by IS NULL THEN RAISE EXCEPTION 'suite_creator_required'; END IF;
    IF NEW.suite_key = ANY(NEW.related_suite_keys) OR
       cardinality(NEW.related_suite_keys) <> (SELECT count(DISTINCT item) FROM unnest(NEW.related_suite_keys) item) OR
       EXISTS (SELECT 1 FROM unnest(NEW.related_suite_keys) item WHERE item IS NULL OR item = '' OR item <> btrim(item)) THEN
        RAISE EXCEPTION 'invalid_related_suite_keys';
    END IF;
    IF NEW.base_revision_id IS NOT NULL THEN
        SELECT * INTO parent FROM product.suite_revisions WHERE id = NEW.base_revision_id;
        IF NOT FOUND OR parent.suite_key <> NEW.suite_key OR
           (parent.tenant_id IS NOT NULL AND parent.tenant_id IS DISTINCT FROM NEW.tenant_id) THEN
            RAISE EXCEPTION 'invalid_suite_base';
        END IF;
    END IF;
    FOR resource IN SELECT key, value FROM jsonb_each_text(NEW.overrides) LOOP
        IF resource.key NOT IN ('rules.json','appendix.json','checker.py','resources.py','templates.json','tests.json') OR
           resource.value !~ '^[a-f0-9]{64}$' OR NOT EXISTS (
                SELECT 1 FROM product.suite_objects WHERE owner_scope = coalesce(NEW.tenant_id::text, 'official') AND digest = resource.value
           ) THEN RAISE EXCEPTION 'invalid_suite_object'; END IF;
    END LOOP;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS check_suite_revision ON product.suite_revisions;
CREATE TRIGGER check_suite_revision BEFORE INSERT ON product.suite_revisions FOR EACH ROW EXECUTE FUNCTION product.check_suite_revision();

CREATE OR REPLACE FUNCTION product.reject_suite_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Only the creator tombstone may change, preserving adopted enterprise assets.
    IF TG_TABLE_NAME = 'suite_revisions' AND TG_OP = 'UPDATE' THEN
        IF OLD.created_by IS NOT NULL AND NEW.created_by IS NULL AND
           (to_jsonb(OLD) - 'created_by') = (to_jsonb(NEW) - 'created_by') THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'immutable_checker_asset';
END;
$$;
DROP TRIGGER IF EXISTS immutable_suite_revision ON product.suite_revisions;
CREATE TRIGGER immutable_suite_revision BEFORE UPDATE OR DELETE ON product.suite_revisions FOR EACH ROW EXECUTE FUNCTION product.reject_suite_mutation();
DROP TRIGGER IF EXISTS immutable_suite_object ON product.suite_objects;
CREATE TRIGGER immutable_suite_object BEFORE UPDATE OR DELETE ON product.suite_objects FOR EACH ROW EXECUTE FUNCTION product.reject_suite_mutation();

CREATE TABLE IF NOT EXISTS product.checker_execution_snapshots (
    round_id uuid PRIMARY KEY REFERENCES product.audit_rounds(id),
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    input_digest text NOT NULL CHECK (input_digest ~ '^[a-f0-9]{64}$'),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((body->>'tenant_id' = tenant_id::text) IS TRUE),
    CHECK ((body->>'round_id' = round_id::text) IS TRUE)
);
CREATE OR REPLACE FUNCTION product.check_execution_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM product.audit_rounds WHERE id=NEW.round_id AND tenant_id=NEW.tenant_id) THEN
        RAISE EXCEPTION 'snapshot_tenant_mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS check_execution_snapshot ON product.checker_execution_snapshots;
CREATE TRIGGER check_execution_snapshot BEFORE INSERT ON product.checker_execution_snapshots FOR EACH ROW EXECUTE FUNCTION product.check_execution_snapshot();
DROP TRIGGER IF EXISTS immutable_checker_snapshot ON product.checker_execution_snapshots;
CREATE TRIGGER immutable_checker_snapshot BEFORE UPDATE OR DELETE ON product.checker_execution_snapshots FOR EACH ROW EXECUTE FUNCTION product.reject_suite_mutation();

GRANT SELECT, INSERT ON product.suite_objects, product.suite_revisions, product.checker_execution_snapshots TO ppap_api;
GRANT SELECT ON product.suite_objects, product.suite_revisions, product.checker_execution_snapshots TO ppap_worker;
INSERT INTO product.schema_migrations(version) VALUES ('0040_checker_suites.sql') ON CONFLICT DO NOTHING;
COMMIT;
