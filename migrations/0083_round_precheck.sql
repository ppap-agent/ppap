BEGIN;

-- The task's precheck (result, cover values and time) frozen with its acceptance. The precheck page,
-- the precheck report and the audit report's C class of an accepted task read only this snapshot.
-- Rounds accepted before it stay NULL: their audit reports carry no precheck warnings.
ALTER TABLE product.audit_rounds ADD COLUMN IF NOT EXISTS precheck jsonb;

INSERT INTO product.schema_migrations (version) VALUES ('0083_round_precheck.sql') ON CONFLICT DO NOTHING;
COMMIT;
