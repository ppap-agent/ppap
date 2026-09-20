CREATE TABLE IF NOT EXISTS product.usage_events (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    user_id uuid NOT NULL REFERENCES product.users (id),
    ai_item_id text NOT NULL,
    consumed_points integer NOT NULL,
    is_free_trial boolean NOT NULL,
    external_status text NOT NULL,
    external_record_id text,
    trace_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT usage_points_nonneg CHECK (consumed_points >= 0),
    CONSTRAINT usage_external_status_known CHECK (
        external_status IN ('none', 'pending', 'confirmed', 'failed')
    )
);

CREATE INDEX IF NOT EXISTS usage_events_tenant_trial_idx
    ON product.usage_events (tenant_id, ai_item_id, is_free_trial, external_status);

CREATE UNIQUE INDEX IF NOT EXISTS usage_events_trace_idx
    ON product.usage_events (tenant_id, trace_id)
    WHERE trace_id IS NOT NULL;

GRANT SELECT, INSERT ON product.usage_events TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0007_usage_events.sql')
ON CONFLICT DO NOTHING;
