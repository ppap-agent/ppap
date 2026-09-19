CREATE TABLE IF NOT EXISTS product.reviews (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id) ON DELETE RESTRICT,
    result_id uuid REFERENCES product.round_results (id) ON DELETE RESTRICT,
    actor_user_id uuid NOT NULL REFERENCES product.users (id),
    judgment text NOT NULL,
    reason text NOT NULL,
    next_action text NOT NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reviews_judgment_known CHECK (judgment IN ('noted', 'questioned', 'action_required')),
    CONSTRAINT reviews_reason_present CHECK (char_length(btrim(reason)) > 0),
    CONSTRAINT reviews_next_action_present CHECK (char_length(btrim(next_action)) > 0),
    CONSTRAINT reviews_actor_round UNIQUE (round_id, actor_user_id)
);

CREATE INDEX IF NOT EXISTS reviews_round_idx ON product.reviews (round_id, updated_at);

GRANT SELECT, INSERT, UPDATE ON product.reviews TO ppap_api;
