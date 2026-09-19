CREATE TABLE IF NOT EXISTS product.usage_events (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id) ON DELETE RESTRICT,
    kind text NOT NULL,
    state text NOT NULL,
    business_key text NOT NULL,
    external_ref text,
    detail text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT usage_kind_known CHECK (kind IN ('audit_round')),
    CONSTRAINT usage_state_known CHECK (state IN (
        'observed', 'trial', 'pending_report', 'confirmed', 'failed', 'unknown', 'reconciling'
    )),
    CONSTRAINT usage_round_unique UNIQUE (tenant_id, round_id),
    CONSTRAINT usage_business_key_unique UNIQUE (tenant_id, business_key)
);

CREATE INDEX IF NOT EXISTS usage_events_tenant_idx ON product.usage_events (tenant_id, created_at);

GRANT SELECT, INSERT, UPDATE ON product.usage_events TO ppap_api;
