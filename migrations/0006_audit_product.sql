GRANT USAGE ON SCHEMA product TO ppap_worker;

CREATE TABLE IF NOT EXISTS product.rule_versions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    title text NOT NULL,
    body jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product.audit_drafts (
    id uuid PRIMARY KEY REFERENCES product.resources (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    rule_version_id uuid REFERENCES product.rule_versions (id),
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT draft_status_known CHECK (status IN ('open', 'accepted'))
);

CREATE TABLE IF NOT EXISTS product.draft_items (
    draft_id uuid NOT NULL REFERENCES product.audit_drafts (id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions (id),
    role text NOT NULL,
    PRIMARY KEY (draft_id, file_version_id),
    CONSTRAINT draft_item_role_known CHECK (role IN ('target', 'reference'))
);

CREATE TABLE IF NOT EXISTS product.audit_rounds (
    id uuid PRIMARY KEY REFERENCES product.resources (id),
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    created_by uuid NOT NULL REFERENCES product.users (id),
    source_draft_id uuid REFERENCES product.audit_drafts (id),
    predecessor_id uuid REFERENCES product.audit_rounds (id),
    rule_version_id uuid NOT NULL REFERENCES product.rule_versions (id),
    rule_title text NOT NULL,
    rule_body jsonb NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint text NOT NULL,
    execution text NOT NULL,
    result_attempt integer,
    coverage jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT round_execution_known CHECK (execution IN ('queued', 'running', 'completed', 'failed')),
    CONSTRAINT rounds_idempotency UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS product.round_inputs (
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id),
    file_version_id uuid NOT NULL REFERENCES product.file_versions (id),
    role text NOT NULL,
    PRIMARY KEY (round_id, file_version_id),
    CONSTRAINT round_input_role_known CHECK (role IN ('target', 'reference'))
);

CREATE TABLE IF NOT EXISTS product.findings (
    id uuid PRIMARY KEY,
    round_id uuid NOT NULL REFERENCES product.audit_rounds (id),
    item_key text NOT NULL,
    classification text NOT NULL,
    item text NOT NULL,
    priority text NOT NULL DEFAULT '',
    status text NOT NULL,
    suggestions text NOT NULL DEFAULT '',
    locator jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT finding_status_known CHECK (status IN ('PASS', 'NG', 'ERR')),
    CONSTRAINT findings_item_key UNIQUE (round_id, item_key)
);

CREATE TABLE IF NOT EXISTS product.reviews (
    round_id uuid NOT NULL,
    item_key text NOT NULL,
    user_id uuid NOT NULL REFERENCES product.users (id),
    judgment text NOT NULL,
    rationale text NOT NULL DEFAULT '',
    next_step text NOT NULL DEFAULT '',
    version integer NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (round_id, item_key),
    CONSTRAINT reviews_finding_fk FOREIGN KEY (round_id, item_key)
        REFERENCES product.findings (round_id, item_key)
);

CREATE INDEX IF NOT EXISTS rule_versions_tenant_idx ON product.rule_versions (tenant_id);
CREATE INDEX IF NOT EXISTS audit_drafts_tenant_idx ON product.audit_drafts (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS audit_rounds_tenant_idx ON product.audit_rounds (tenant_id, created_at);
CREATE INDEX IF NOT EXISTS findings_round_idx ON product.findings (round_id);

GRANT SELECT, INSERT, UPDATE ON
    product.rule_versions,
    product.audit_drafts,
    product.draft_items,
    product.audit_rounds,
    product.round_inputs,
    product.findings,
    product.reviews
TO ppap_api;
GRANT DELETE ON product.draft_items TO ppap_api;

GRANT SELECT ON
    product.rule_versions,
    product.audit_rounds,
    product.round_inputs,
    product.findings,
    product.file_versions
TO ppap_worker;
GRANT UPDATE ON product.audit_rounds TO ppap_worker;
GRANT INSERT, UPDATE ON product.findings TO ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0006_audit_product.sql')
ON CONFLICT DO NOTHING;
