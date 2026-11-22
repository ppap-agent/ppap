BEGIN;

-- Recognition is created only by task acceptance: it names its task, the task revision it
-- belongs to and the candidate rules frozen for it. Nothing reads all of an enterprise's rules.
ALTER TABLE engine.jobs DROP CONSTRAINT IF EXISTS file_analysis_task_bound;
ALTER TABLE engine.jobs ADD CONSTRAINT file_analysis_task_bound CHECK (
    kind <> 'file_analysis' OR COALESCE(
        jsonb_typeof(payload->'task_id') = 'string'
        AND jsonb_typeof(payload->'task_revision') = 'number'
        AND payload->>'task_revision' ~ '^[0-9]+$'
        AND jsonb_typeof(payload->'candidate_rules') = 'array',
        false
    )
);

-- Provider credentials live only in provider_authorizations (0035 emptied these copies).
-- The runner replays every migration: 0004 and 0035 re-add the columns empty and this file drops them again.
ALTER TABLE product.sessions
    DROP COLUMN IF EXISTS access_token,
    DROP COLUMN IF EXISTS refresh_token,
    DROP COLUMN IF EXISTS access_expires_at,
    DROP COLUMN IF EXISTS refresh_expires_at;
ALTER TABLE product.mcp_grants
    DROP COLUMN IF EXISTS access_token,
    DROP COLUMN IF EXISTS refresh_token,
    DROP COLUMN IF EXISTS access_expires_at,
    DROP COLUMN IF EXISTS refresh_expires_at,
    DROP COLUMN IF EXISTS provider_refresh_state;

INSERT INTO product.schema_migrations (version) VALUES ('0088_recognition_shape_and_session_credentials.sql') ON CONFLICT DO NOTHING;
COMMIT;
