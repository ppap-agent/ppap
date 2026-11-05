BEGIN;

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
        -- A generated program may remove its optional module. Keep the base and
        -- its other assets while making the candidate's exact byte set explicit.
        IF resource.value IS NULL THEN
            IF resource.key <> 'resources.py' OR NEW.base_revision_id IS NULL THEN
                RAISE EXCEPTION 'invalid_suite_deleted_path';
            END IF;
            CONTINUE;
        END IF;
        IF resource.key NOT IN ('rules.json','appendix.json','checker.py','resources.py','templates.json','tests.json') OR
           resource.value !~ '^[a-f0-9]{64}$' OR NOT EXISTS (
                SELECT 1 FROM product.suite_objects WHERE owner_scope = coalesce(NEW.tenant_id::text, 'official') AND digest = resource.value
           ) THEN RAISE EXCEPTION 'invalid_suite_object'; END IF;
    END LOOP;
    RETURN NEW;
END;
$$;

INSERT INTO product.schema_migrations(version) VALUES ('0046_checker_suite_tombstones.sql') ON CONFLICT DO NOTHING;
COMMIT;
