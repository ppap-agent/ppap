BEGIN;

-- A Suite revision never changes, so what the catalog shows about it never changes either.
-- These facts are derived once from the verified revision bytes and read by set queries,
-- instead of re-reading every object of every Suite on each catalog read.
-- facts_version names the derivation; a new derivation adds rows, it never rewrites old ones.
CREATE TABLE IF NOT EXISTS product.suite_revision_facts (
    revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    facts_version integer NOT NULL CHECK (facts_version > 0),
    tenant_id uuid REFERENCES product.tenants(id),
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
    rule_digest text NOT NULL CHECK (rule_digest ~ '^[a-f0-9]{64}$'),
    capability jsonb NOT NULL CHECK (jsonb_typeof(capability) = 'object'),
    source_identity_valid boolean,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (revision_id, facts_version)
);

CREATE OR REPLACE FUNCTION product.check_suite_revision_facts() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM product.suite_revisions
        WHERE id = NEW.revision_id AND tenant_id IS NOT DISTINCT FROM NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'suite_facts_tenant_mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS check_suite_revision_facts ON product.suite_revision_facts;
CREATE TRIGGER check_suite_revision_facts BEFORE INSERT ON product.suite_revision_facts
    FOR EACH ROW EXECUTE FUNCTION product.check_suite_revision_facts();
DROP TRIGGER IF EXISTS immutable_suite_revision_facts ON product.suite_revision_facts;
CREATE TRIGGER immutable_suite_revision_facts BEFORE UPDATE OR DELETE ON product.suite_revision_facts
    FOR EACH ROW EXECUTE FUNCTION product.reject_suite_mutation();

GRANT SELECT, INSERT ON product.suite_revision_facts TO ppap_api, ppap_worker;

INSERT INTO product.schema_migrations(version) VALUES ('0061_suite_revision_facts.sql') ON CONFLICT DO NOTHING;
COMMIT;
