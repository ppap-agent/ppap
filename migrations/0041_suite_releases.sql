BEGIN;

-- One tenant default per Suite. A rollback is another compare-and-swap publication.
CREATE TABLE IF NOT EXISTS product.tenant_suite_heads (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    suite_key text NOT NULL,
    current_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    revision bigint NOT NULL CHECK (revision > 0),
    force_unverified boolean NOT NULL DEFAULT false,
    published_by uuid REFERENCES product.users(id) ON DELETE SET NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, suite_key)
);
CREATE TABLE IF NOT EXISTS product.suite_validations (
    tenant_id uuid REFERENCES product.tenants(id),
    scope_key text GENERATED ALWAYS AS (coalesce(tenant_id::text, 'official')) STORED,
    revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    status text NOT NULL CHECK (status IN ('verified', 'unverified', 'stale')),
    revision bigint NOT NULL CHECK (revision > 0),
    suite_digest text NOT NULL,
    related_suite_digests jsonb NOT NULL DEFAULT '{}'::jsonb,
    proof jsonb NOT NULL,
    recorded_by uuid REFERENCES product.users(id) ON DELETE SET NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_key, revision_id)
);
CREATE TABLE IF NOT EXISTS product.suite_publications (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    operation_id uuid NOT NULL,
    suite_key text NOT NULL,
    revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    previous_revision_id uuid REFERENCES product.suite_revisions(id),
    expected_revision bigint NOT NULL,
    revision bigint NOT NULL,
    request_fingerprint text NOT NULL,
    verification_status text NOT NULL CHECK (verification_status IN ('verified', 'unverified', 'stale')),
    force_unverified boolean NOT NULL,
    published_by uuid REFERENCES product.users(id) ON DELETE SET NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, operation_id),
    UNIQUE (tenant_id, suite_key, revision),
    CHECK (revision = expected_revision + 1),
    CHECK (verification_status = 'verified' OR force_unverified)
);

CREATE OR REPLACE FUNCTION product.validate_suite_release_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item product.suite_revisions%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME = 'tenant_suite_heads' THEN
        SELECT * INTO item FROM product.suite_revisions WHERE id = NEW.current_revision_id;
    ELSE
        SELECT * INTO item FROM product.suite_revisions WHERE id = NEW.revision_id;
    END IF;
    IF item.id IS NULL OR (item.tenant_id IS NOT NULL AND item.tenant_id IS DISTINCT FROM NEW.tenant_id) THEN
        RAISE EXCEPTION 'suite_tenant_mismatch' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'suite_validations' THEN
        IF TG_OP = 'UPDATE' AND (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.revision_id <> OLD.revision_id) THEN
            RAISE EXCEPTION 'immutable_suite_validation_scope' USING ERRCODE = '23514';
        END IF;
        IF NEW.tenant_id IS NULL AND current_user <> 'ppap_migrator' THEN
            RAISE EXCEPTION 'official_validation_write_forbidden';
        END IF;
    ELSIF item.suite_key <> NEW.suite_key THEN
        RAISE EXCEPTION 'suite_key_mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS suite_head_scope ON product.tenant_suite_heads;
CREATE TRIGGER suite_head_scope BEFORE INSERT OR UPDATE ON product.tenant_suite_heads
    FOR EACH ROW EXECUTE FUNCTION product.validate_suite_release_scope();
DROP TRIGGER IF EXISTS suite_validation_scope ON product.suite_validations;
CREATE TRIGGER suite_validation_scope BEFORE INSERT OR UPDATE ON product.suite_validations
    FOR EACH ROW EXECUTE FUNCTION product.validate_suite_release_scope();
DROP TRIGGER IF EXISTS suite_publication_scope ON product.suite_publications;
CREATE TRIGGER suite_publication_scope BEFORE INSERT OR UPDATE ON product.suite_publications
    FOR EACH ROW EXECUTE FUNCTION product.validate_suite_release_scope();

CREATE OR REPLACE FUNCTION product.immutable_suite_publication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'published_by') IS DISTINCT FROM (to_jsonb(OLD) - 'published_by')
       OR NEW.published_by IS NOT NULL THEN
        RAISE EXCEPTION 'immutable_suite_publication' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS suite_publication_immutable ON product.suite_publications;
CREATE TRIGGER suite_publication_immutable BEFORE UPDATE ON product.suite_publications
    FOR EACH ROW EXECUTE FUNCTION product.immutable_suite_publication();

GRANT SELECT, INSERT, UPDATE ON product.tenant_suite_heads, product.suite_validations TO ppap_api;
GRANT SELECT, INSERT ON product.suite_publications TO ppap_api;
GRANT SELECT ON product.tenant_suite_heads, product.suite_publications TO ppap_worker;
-- Host-fenced optimizer completion records tenant proofs; the scope trigger still
-- forbids worker writes to official proofs. There is no client proof endpoint.
GRANT SELECT, INSERT, UPDATE ON product.suite_validations TO ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0041_suite_releases.sql') ON CONFLICT DO NOTHING;
COMMIT;
