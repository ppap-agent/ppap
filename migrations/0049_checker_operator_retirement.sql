BEGIN;

-- Confirmed samples and completed development receipts belong to the tenant.
-- Removing their operator must not remove published bytes or historical proof.
ALTER TABLE product.checker_feedback ALTER COLUMN created_by DROP NOT NULL;
ALTER TABLE product.checker_feedback DROP CONSTRAINT IF EXISTS checker_feedback_created_by_fkey;
ALTER TABLE product.checker_feedback ADD CONSTRAINT checker_feedback_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES product.users(id) ON DELETE SET NULL;
ALTER TABLE product.checker_optimizations ALTER COLUMN initiating_user_id DROP NOT NULL;
ALTER TABLE product.checker_optimizations DROP CONSTRAINT IF EXISTS checker_optimizations_initiating_user_id_fkey;
ALTER TABLE product.checker_optimizations ADD CONSTRAINT checker_optimizations_initiating_user_id_fkey
    FOREIGN KEY (initiating_user_id) REFERENCES product.users(id) ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION product.retire_checker_optimizations(operator_id uuid, scope_id uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE operation record;
BEGIN
    -- Lock in the same order as dispatch/finish: operation, then job. Membership
    -- removal and the execution fence commit together; reconnecting cannot retry.
    FOR operation IN
        UPDATE product.checker_optimizations
        SET cancel_requested=true,
            state=CASE WHEN model_started_at IS NOT NULL AND frozen->>'consume_model'='true' THEN 'unknown' ELSE 'stopped' END,
            usage=CASE WHEN model_started_at IS NOT NULL AND frozen->>'consume_model'='true'
                       THEN usage || '{"uncertain":true}'::jsonb ELSE usage END,
            failure='checker_operator_removed', completed_at=clock_timestamp()
        WHERE initiating_user_id=operator_id AND (scope_id IS NULL OR tenant_id=scope_id)
          AND state IN ('queued','running')
        RETURNING operation_id, job_id, state
    LOOP
        UPDATE engine.jobs SET status='cancelled', lease_until=NULL, updated_at=clock_timestamp(),
            result=jsonb_build_object('operation_id', operation.operation_id, 'state', operation.state, 'failure', 'checker_operator_removed')
        WHERE id=operation.job_id AND status IN ('queued','leased');
        UPDATE engine.attempts SET finished_at=clock_timestamp(), outcome='cancelled',
            diagnostic=jsonb_build_object('state', operation.state, 'failure', 'checker_operator_removed')
        WHERE job_id=operation.job_id AND finished_at IS NULL;
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION product.retire_checker_optimizations(uuid, uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION product.retire_checker_operator() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'memberships' THEN
        PERFORM product.retire_checker_optimizations(OLD.user_id, OLD.tenant_id);
    ELSE
        PERFORM product.retire_checker_optimizations(OLD.id, NULL);
    END IF;
    RETURN OLD;
END;
$$;
REVOKE ALL ON FUNCTION product.retire_checker_operator() FROM PUBLIC;
DROP TRIGGER IF EXISTS retire_checker_member ON product.memberships;
CREATE TRIGGER retire_checker_member AFTER DELETE ON product.memberships
    FOR EACH ROW EXECUTE FUNCTION product.retire_checker_operator();
DROP TRIGGER IF EXISTS retire_checker_user ON product.users;
CREATE TRIGGER retire_checker_user BEFORE DELETE ON product.users
    FOR EACH ROW EXECUTE FUNCTION product.retire_checker_operator();

-- Upgrading also closes work already orphaned by earlier membership removals.
SELECT product.retire_checker_optimizations(o.initiating_user_id, o.tenant_id)
FROM (SELECT DISTINCT initiating_user_id, tenant_id FROM product.checker_optimizations
      WHERE state IN ('queued','running')) o
WHERE NOT EXISTS (SELECT 1 FROM product.memberships m WHERE m.user_id=o.initiating_user_id AND m.tenant_id=o.tenant_id);

INSERT INTO product.schema_migrations(version) VALUES ('0049_checker_operator_retirement.sql') ON CONFLICT DO NOTHING;
COMMIT;
