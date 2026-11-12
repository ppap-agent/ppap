BEGIN;

-- Task change notices for live views. A committed change to a task, its recognition
-- items, rounds, findings, reviews, report exports, display corrections, jobs or job
-- attempts sends pg_notify('audit_task_events', {"tenant","task","kind"}) at commit.
-- The notice only says "this task changed"; readers fetch the task through the
-- authorized API. Identical notices in one transaction are delivered once.
-- A notice never blocks or fails the business write that caused it.

CREATE OR REPLACE FUNCTION product.notify_task_event(p_tenant uuid, p_task uuid, p_kind text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF p_task IS NOT NULL THEN
        PERFORM pg_notify('audit_task_events', json_build_object('tenant', p_tenant, 'task', p_task, 'kind', p_kind)::text);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION product.task_of_round(p_round uuid)
RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, product AS $$
    SELECT source_draft_id FROM product.audit_rounds WHERE id = p_round
$$;

CREATE OR REPLACE FUNCTION product.task_of_job(p_job_id uuid, p_round uuid, p_payload jsonb)
RETURNS uuid LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, product AS $$
DECLARE
    found uuid;
BEGIN
    IF p_round IS NOT NULL THEN
        SELECT source_draft_id INTO found FROM product.audit_rounds WHERE id = p_round;
        IF found IS NOT NULL THEN RETURN found; END IF;
    END IF;
    IF coalesce(p_payload->>'task_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN (p_payload->>'task_id')::uuid;
    END IF;
    SELECT draft_id INTO found FROM product.draft_items WHERE analysis_job_id = p_job_id LIMIT 1;
    RETURN found;
END $$;

CREATE OR REPLACE FUNCTION product.audit_task_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, product, engine AS $$
DECLARE
    row_data jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
    old_data jsonb := CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) ELSE NULL END;
    tenant uuid;
    task uuid;
    kind text := 'state';
    job record;
BEGIN
    BEGIN
        IF TG_TABLE_SCHEMA = 'product' AND TG_TABLE_NAME = 'audit_drafts' THEN
            IF row_data->>'task_fingerprint' IS NULL THEN RETURN NULL; END IF;
            task := (row_data->>'id')::uuid;
            tenant := (row_data->>'tenant_id')::uuid;
        ELSIF TG_TABLE_NAME IN ('draft_items') THEN
            task := (row_data->>'draft_id')::uuid;
        ELSIF TG_TABLE_NAME = 'audit_display_requests' THEN
            task := (row_data->>'task_id')::uuid;
        ELSIF TG_TABLE_NAME = 'audit_rounds' THEN
            task := (row_data->>'source_draft_id')::uuid;
            tenant := (row_data->>'tenant_id')::uuid;
        ELSIF TG_TABLE_NAME IN ('findings', 'reviews', 'report_exports', 'round_inputs') THEN
            task := product.task_of_round((row_data->>'round_id')::uuid);
        ELSIF TG_TABLE_SCHEMA = 'engine' AND TG_TABLE_NAME = 'jobs' THEN
            -- Lease renewals do not change what a reader sees.
            IF TG_OP = 'UPDATE' AND old_data->'status' IS NOT DISTINCT FROM row_data->'status'
               AND old_data->'result' IS NOT DISTINCT FROM row_data->'result' THEN
                RETURN NULL;
            END IF;
            tenant := (row_data->>'tenant_id')::uuid;
            task := product.task_of_job((row_data->>'id')::uuid, (row_data->>'audit_round_id')::uuid, row_data->'payload');
        ELSIF TG_TABLE_SCHEMA = 'engine' AND TG_TABLE_NAME = 'attempts' THEN
            IF TG_OP = 'UPDATE' AND old_data->'diagnostic' IS NOT DISTINCT FROM row_data->'diagnostic'
               AND old_data->'finished_at' IS NOT DISTINCT FROM row_data->'finished_at' THEN
                RETURN NULL;
            END IF;
            SELECT j.id, j.tenant_id, j.audit_round_id, j.payload INTO job FROM engine.jobs j WHERE j.id = (row_data->>'job_id')::uuid;
            IF job.id IS NULL THEN RETURN NULL; END IF;
            tenant := job.tenant_id;
            task := product.task_of_job(job.id, job.audit_round_id, job.payload);
            IF TG_OP = 'UPDATE' AND old_data->'finished_at' IS NOT DISTINCT FROM row_data->'finished_at' THEN
                kind := 'progress';
            END IF;
        END IF;
        IF task IS NOT NULL AND tenant IS NULL THEN
            SELECT d.tenant_id INTO tenant FROM product.audit_drafts d WHERE d.id = task;
        END IF;
        PERFORM product.notify_task_event(tenant, task, kind);
    EXCEPTION WHEN OTHERS THEN
        -- A live-view notice is never worth failing the business write.
        NULL;
    END;
    RETURN NULL;
END $$;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'product.audit_drafts', 'product.draft_items', 'product.audit_display_requests', 'product.audit_rounds',
        'product.round_inputs', 'product.findings', 'product.reviews', 'product.report_exports',
        'engine.jobs', 'engine.attempts'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS audit_task_event ON %s', target);
        EXECUTE format('CREATE TRIGGER audit_task_event AFTER INSERT OR UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION product.audit_task_event()', target);
    END LOOP;
END $$;

INSERT INTO product.schema_migrations (version) VALUES ('0073_task_events.sql') ON CONFLICT DO NOTHING;
COMMIT;
