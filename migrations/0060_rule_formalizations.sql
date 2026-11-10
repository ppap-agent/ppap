BEGIN;

-- Confirmed meaning stays on the published rule version. Rows are immutable.
-- A later revision is a new row. Confirmation history is a pointer, not a second rule tree.
CREATE TABLE IF NOT EXISTS product.rule_formalizations (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    rule_version_id uuid NOT NULL REFERENCES product.rule_versions(id),
    clause_id text NOT NULL CHECK (char_length(clause_id) BETWEEN 1 AND 200),
    formalization_revision integer NOT NULL CHECK (formalization_revision > 0),
    parent_candidate_id uuid REFERENCES product.rule_formalizations(id),
    source_rule_digest text NOT NULL CHECK (source_rule_digest ~ '^[a-f0-9]{64}$'),
    formal_profile text NOT NULL CHECK (formal_profile IN ('owl2-dl-v1', 'owl2-plus-clif-v1')),
    owl_content text NOT NULL CHECK (char_length(owl_content) BETWEEN 1 AND 65536),
    clif_content text CHECK (clif_content IS NULL OR char_length(clif_content) BETWEEN 1 AND 65536),
    vocabulary jsonb NOT NULL CHECK (jsonb_typeof(vocabulary) = 'object'),
    obligations jsonb NOT NULL CHECK (jsonb_typeof(obligations) = 'array'),
    assumptions jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'array'),
    unresolved jsonb NOT NULL CHECK (jsonb_typeof(unresolved) = 'array'),
    completeness text NOT NULL CHECK (completeness IN ('complete', 'partial')),
    cnl_text text NOT NULL CHECK (char_length(cnl_text) BETWEEN 1 AND 32768),
    cnl_renderer_version text NOT NULL CHECK (char_length(cnl_renderer_version) BETWEEN 1 AND 40),
    formal_digest text NOT NULL CHECK (formal_digest ~ '^[a-f0-9]{64}$'),
    display_digest text NOT NULL CHECK (display_digest ~ '^[a-f0-9]{64}$'),
    created_by uuid NOT NULL REFERENCES product.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    operation_id uuid,
    UNIQUE (rule_version_id, clause_id, formalization_revision),
    CHECK (
        (formal_profile = 'owl2-dl-v1' AND clif_content IS NULL)
        OR formal_profile = 'owl2-plus-clif-v1'
    ),
    CHECK (completeness <> 'complete' OR jsonb_array_length(unresolved) = 0)
);

CREATE INDEX IF NOT EXISTS rule_formalizations_clause
    ON product.rule_formalizations (tenant_id, rule_version_id, clause_id, formalization_revision);

CREATE TABLE IF NOT EXISTS product.rule_formalization_operations (
    operation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    initiating_user_id uuid NOT NULL REFERENCES product.users(id),
    rule_version_id uuid NOT NULL REFERENCES product.rule_versions(id),
    clause_id text NOT NULL,
    request_fingerprint text NOT NULL,
    source_rule_digest text NOT NULL CHECK (source_rule_digest ~ '^[a-f0-9]{64}$'),
    based_on_candidate_id uuid REFERENCES product.rule_formalizations(id),
    clarification jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(clarification) = 'object'),
    user_instruction text CHECK (user_instruction IS NULL OR char_length(user_instruction) <= 2000),
    model_identity jsonb NOT NULL CHECK (jsonb_typeof(model_identity) = 'object'),
    job_id uuid UNIQUE REFERENCES engine.jobs(id),
    state text NOT NULL CHECK (state IN ('queued', 'running', 'completed', 'failed', 'unknown', 'stopped')),
    candidate_id uuid REFERENCES product.rule_formalizations(id),
    usage jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(usage) = 'object'),
    failure text,
    model_started_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS rule_formalization_operations_owner
    ON product.rule_formalization_operations (tenant_id, initiating_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS product.rule_formalization_confirmations (
    id uuid PRIMARY KEY,
    operation_id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    rule_version_id uuid NOT NULL REFERENCES product.rule_versions(id),
    clause_id text NOT NULL,
    candidate_id uuid NOT NULL REFERENCES product.rule_formalizations(id),
    formalization_revision integer NOT NULL CHECK (formalization_revision > 0),
    source_rule_digest text NOT NULL CHECK (source_rule_digest ~ '^[a-f0-9]{64}$'),
    display_digest text NOT NULL CHECK (display_digest ~ '^[a-f0-9]{64}$'),
    confirmed_by uuid NOT NULL REFERENCES product.users(id),
    confirmed_at timestamptz NOT NULL DEFAULT now(),
    current boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, operation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS rule_formalization_one_current_confirmation
    ON product.rule_formalization_confirmations (tenant_id, rule_version_id, clause_id)
    WHERE current;

CREATE OR REPLACE FUNCTION product.rule_formalization_belongs() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    owner uuid;
    rules jsonb;
BEGIN
    SELECT tenant_id, body->'rules' INTO owner, rules
    FROM product.rule_versions WHERE id = NEW.rule_version_id;
    IF owner IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION 'formalization_rule_tenant' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(COALESCE(rules, '[]'::jsonb)) clause
        WHERE clause->>'id' = NEW.clause_id
    ) THEN
        RAISE EXCEPTION 'formalization_clause_missing' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS rule_formalizations_belong ON product.rule_formalizations;
CREATE TRIGGER rule_formalizations_belong
    BEFORE INSERT ON product.rule_formalizations
    FOR EACH ROW EXECUTE FUNCTION product.rule_formalization_belongs();

DROP TRIGGER IF EXISTS rule_formalization_operations_belong ON product.rule_formalization_operations;
CREATE TRIGGER rule_formalization_operations_belong
    BEFORE INSERT ON product.rule_formalization_operations
    FOR EACH ROW EXECUTE FUNCTION product.rule_formalization_belongs();

DROP TRIGGER IF EXISTS rule_formalization_confirmations_belong ON product.rule_formalization_confirmations;
CREATE TRIGGER rule_formalization_confirmations_belong
    BEFORE INSERT ON product.rule_formalization_confirmations
    FOR EACH ROW EXECUTE FUNCTION product.rule_formalization_belongs();

CREATE OR REPLACE FUNCTION product.rule_formalizations_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'formalization_immutable' USING ERRCODE = '55000';
END $$;

DROP TRIGGER IF EXISTS rule_formalizations_no_change ON product.rule_formalizations;
CREATE TRIGGER rule_formalizations_no_change
    BEFORE UPDATE OR DELETE ON product.rule_formalizations
    FOR EACH ROW EXECUTE FUNCTION product.rule_formalizations_immutable();

CREATE OR REPLACE FUNCTION product.rule_formalization_confirmation_pointer() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'formalization_confirmation_immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.current IS TRUE OR OLD.current IS FALSE
        OR NEW.id IS DISTINCT FROM OLD.id
        OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
        OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
        OR NEW.rule_version_id IS DISTINCT FROM OLD.rule_version_id
        OR NEW.clause_id IS DISTINCT FROM OLD.clause_id
        OR NEW.candidate_id IS DISTINCT FROM OLD.candidate_id
        OR NEW.formalization_revision IS DISTINCT FROM OLD.formalization_revision
        OR NEW.source_rule_digest IS DISTINCT FROM OLD.source_rule_digest
        OR NEW.display_digest IS DISTINCT FROM OLD.display_digest
        OR NEW.confirmed_by IS DISTINCT FROM OLD.confirmed_by
        OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
    THEN
        RAISE EXCEPTION 'formalization_confirmation_immutable' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS rule_formalization_confirmations_pointer ON product.rule_formalization_confirmations;
CREATE TRIGGER rule_formalization_confirmations_pointer
    BEFORE UPDATE OR DELETE ON product.rule_formalization_confirmations
    FOR EACH ROW EXECUTE FUNCTION product.rule_formalization_confirmation_pointer();

GRANT SELECT, INSERT ON product.rule_formalizations TO ppap_api, ppap_worker;
GRANT SELECT, INSERT, UPDATE ON product.rule_formalization_operations TO ppap_api, ppap_worker;
GRANT SELECT, INSERT, UPDATE ON product.rule_formalization_confirmations TO ppap_api;

INSERT INTO product.schema_migrations(version) VALUES ('0060_rule_formalizations.sql') ON CONFLICT DO NOTHING;
COMMIT;
