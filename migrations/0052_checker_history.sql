BEGIN;

-- Read-only provenance on the original inputs, not a second Round ledger and
-- not a fabricated admission snapshot for the new execution engine.
ALTER TABLE product.suite_revisions ADD COLUMN IF NOT EXISTS history_only boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS suite_historical_content ON product.suite_revisions(tenant_id,suite_key,resolved_digest) WHERE history_only;
CREATE OR REPLACE FUNCTION product.check_history_suite() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.history_only AND (current_user <> 'ppap_migrator' OR NEW.tenant_id IS NULL) THEN
        RAISE EXCEPTION 'history_suite_migrator_required';
    END IF;
    IF NEW.base_revision_id IS NOT NULL AND EXISTS (SELECT 1 FROM product.suite_revisions WHERE id=NEW.base_revision_id AND history_only) THEN
        RAISE EXCEPTION 'history_suite_not_executable';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS check_history_suite ON product.suite_revisions;
CREATE TRIGGER check_history_suite BEFORE INSERT ON product.suite_revisions
    FOR EACH ROW EXECUTE FUNCTION product.check_history_suite();

-- Defense in depth: an archive cannot acquire executable release/proof state,
-- even through a direct write using an application database role.
CREATE OR REPLACE FUNCTION product.reject_history_release() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE revision_id uuid;
BEGIN
    revision_id := coalesce(to_jsonb(NEW)->>'current_revision_id', to_jsonb(NEW)->>'revision_id')::uuid;
    IF EXISTS (SELECT 1 FROM product.suite_revisions WHERE id=revision_id AND history_only) THEN
        RAISE EXCEPTION 'history_suite_not_executable';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS reject_history_release ON product.tenant_suite_heads;
CREATE TRIGGER reject_history_release BEFORE INSERT OR UPDATE ON product.tenant_suite_heads
    FOR EACH ROW EXECUTE FUNCTION product.reject_history_release();
DROP TRIGGER IF EXISTS reject_history_release ON product.suite_validations;
CREATE TRIGGER reject_history_release BEFORE INSERT OR UPDATE ON product.suite_validations
    FOR EACH ROW EXECUTE FUNCTION product.reject_history_release();
DROP TRIGGER IF EXISTS reject_history_release ON product.suite_publications;
CREATE TRIGGER reject_history_release BEFORE INSERT OR UPDATE ON product.suite_publications
    FOR EACH ROW EXECUTE FUNCTION product.reject_history_release();

ALTER TABLE product.round_inputs ADD COLUMN IF NOT EXISTS checker_history jsonb;
ALTER TABLE product.round_inputs DROP CONSTRAINT IF EXISTS checker_history_shape;
ALTER TABLE product.round_inputs ADD CONSTRAINT checker_history_shape CHECK (
    checker_history IS NULL OR (
        jsonb_typeof(checker_history) = 'object' AND
        (checker_history->>'schema' = 'checker-history-v1') IS TRUE AND
        (checker_history->>'round_id' = round_id::text) IS TRUE AND
        (checker_history->>'file_version_id' = file_version_id::text) IS TRUE AND
        (checker_history->>'source_digest' ~ '^[a-f0-9]{64}$') IS TRUE AND
        (checker_history->>'verification' = 'unverified') IS TRUE
    )
);

CREATE OR REPLACE FUNCTION product.check_history_import() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_id uuid; suite_id uuid;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.checker_history IS NOT NULL THEN
        IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'immutable_checker_history'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.checker_history IS NULL THEN RETURN NEW; END IF;
    IF current_user <> 'ppap_migrator' THEN RAISE EXCEPTION 'checker_history_migrator_required'; END IF;
    SELECT tenant_id INTO owner_id FROM product.audit_rounds WHERE id=NEW.round_id AND execution IN ('completed','failed');
    IF owner_id IS NULL OR NEW.checker_history->>'tenant_id' IS DISTINCT FROM owner_id::text OR
       NOT EXISTS (SELECT 1 FROM product.file_versions WHERE id=NEW.file_version_id AND tenant_id=owner_id) OR
       EXISTS (SELECT 1 FROM product.checker_execution_snapshots WHERE round_id=NEW.round_id) THEN
        RAISE EXCEPTION 'checker_history_source_mismatch';
    END IF;
    suite_id := (NEW.checker_history->'artifact'->>'suite_revision_id')::uuid;
    IF suite_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM product.suite_revisions WHERE id=suite_id AND tenant_id=owner_id AND history_only) THEN
        RAISE EXCEPTION 'checker_history_suite_mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS check_history_import ON product.round_inputs;
CREATE TRIGGER check_history_import BEFORE INSERT OR UPDATE ON product.round_inputs
    FOR EACH ROW EXECUTE FUNCTION product.check_history_import();

INSERT INTO product.schema_migrations(version) VALUES ('0052_checker_history.sql') ON CONFLICT DO NOTHING;
COMMIT;
