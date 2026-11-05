BEGIN;

-- Saving a platform source or its regression proof is not publication. Existing
-- tenant pins and historical snapshots are unchanged; no legacy latest row is
-- guessed to be an approved release during upgrade.
CREATE TABLE IF NOT EXISTS product.official_suite_heads (
    suite_key text PRIMARY KEY,
    current_revision_id uuid NOT NULL REFERENCES product.suite_revisions(id),
    revision bigint NOT NULL CHECK (revision > 0),
    validation_revision bigint NOT NULL CHECK (validation_revision > 0),
    published_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION product.validate_official_suite_head() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item product.suite_revisions%ROWTYPE; proof product.suite_validations%ROWTYPE;
BEGIN
    IF current_user <> 'ppap_migrator' THEN RAISE EXCEPTION 'official_suite_write_forbidden'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('suite-publication:official', 0));
    IF (TG_OP = 'INSERT' AND NEW.revision <> 1) OR
       (TG_OP = 'UPDATE' AND (NEW.suite_key <> OLD.suite_key OR NEW.revision <> OLD.revision + 1)) THEN
        RAISE EXCEPTION 'suite_head_conflict';
    END IF;
    SELECT * INTO item FROM product.suite_revisions WHERE id = NEW.current_revision_id;
    IF item.id IS NULL OR item.tenant_id IS NOT NULL OR item.suite_key <> NEW.suite_key THEN
        RAISE EXCEPTION 'official_publication_requires_official_suite';
    END IF;
    SELECT * INTO proof FROM product.suite_validations WHERE tenant_id IS NULL AND revision_id = item.id;
    IF proof.revision IS DISTINCT FROM NEW.validation_revision OR proof.status IS DISTINCT FROM 'verified' OR
       proof.suite_digest IS DISTINCT FROM item.resolved_digest OR
       (SELECT count(*) FROM jsonb_object_keys(proof.related_suite_digests)) <> cardinality(item.related_suite_keys) OR
       EXISTS (
           SELECT 1 FROM unnest(item.related_suite_keys) dependency
           LEFT JOIN product.official_suite_heads h ON h.suite_key = dependency
           LEFT JOIN product.suite_revisions r ON r.id = h.current_revision_id
           WHERE r.id IS NULL OR proof.related_suite_digests->>dependency IS DISTINCT FROM r.resolved_digest
       ) THEN
        RAISE EXCEPTION 'official_suite_validation_required';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS official_suite_head_scope ON product.official_suite_heads;
CREATE TRIGGER official_suite_head_scope BEFORE INSERT OR UPDATE ON product.official_suite_heads
    FOR EACH ROW EXECUTE FUNCTION product.validate_official_suite_head();

REVOKE ALL ON product.official_suite_heads FROM ppap_api, ppap_worker;
GRANT SELECT ON product.official_suite_heads TO ppap_api, ppap_worker;
INSERT INTO product.schema_migrations(version) VALUES ('0048_official_suite_heads.sql') ON CONFLICT DO NOTHING;
COMMIT;
