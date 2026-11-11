BEGIN;

-- A checker shipped in the repository is already validated offline. The platform makes it the
-- official default directly; the repository, not a corpus proof row, is its validation.
ALTER TABLE product.official_suite_heads ADD COLUMN IF NOT EXISTS repository_default boolean NOT NULL DEFAULT false;
ALTER TABLE product.official_suite_heads ALTER COLUMN validation_revision DROP NOT NULL;
ALTER TABLE product.official_suite_heads DROP CONSTRAINT IF EXISTS official_suite_heads_validation;
ALTER TABLE product.official_suite_heads ADD CONSTRAINT official_suite_heads_validation CHECK (validation_revision IS NOT NULL OR repository_default);

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
    IF NEW.repository_default THEN
        -- No proof row is claimed; every related program must already have an official default.
        IF NEW.validation_revision IS NOT NULL OR EXISTS (
               SELECT 1 FROM unnest(item.related_suite_keys) dependency
               LEFT JOIN product.official_suite_heads h ON h.suite_key = dependency
               WHERE h.suite_key IS NULL
           ) THEN
            RAISE EXCEPTION 'official_suite_validation_required';
        END IF;
        RETURN NEW;
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

INSERT INTO product.schema_migrations (version) VALUES ('0067_repository_default_heads.sql') ON CONFLICT DO NOTHING;
COMMIT;
