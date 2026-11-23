BEGIN;

-- 「这个版本不再提醒」(#354): one person's choice not to be told on Feishu when customer answers arrive on a rulebook
-- version being clarified. Kept per person and version, for every revision of it; the answers stay readable on the
-- rules page either way. Turning it back on updates the same row.
CREATE TABLE IF NOT EXISTS product.rule_answer_alerts (
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    user_id uuid NOT NULL REFERENCES product.users(id),
    customer_id text NOT NULL CHECK (length(customer_id) BETWEEN 1 AND 64),
    rulebook_version text NOT NULL CHECK (length(rulebook_version) BETWEEN 1 AND 200),
    muted boolean NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, customer_id, rulebook_version)
);
GRANT SELECT, INSERT, UPDATE ON product.rule_answer_alerts TO ppap_api;
REVOKE DELETE ON product.rule_answer_alerts FROM ppap_api;
REVOKE ALL ON product.rule_answer_alerts FROM ppap_worker;

-- Each answer looks up the latest notice of answers to the same person on the same questionnaire.
CREATE INDEX IF NOT EXISTS rule_answers_notification_latest ON engine.jobs
    ((payload->>'questionnaire_id'), (payload->>'user_id'), created_at DESC)
    WHERE kind = 'rule_answers_notification';

INSERT INTO product.schema_migrations (version) VALUES ('0094_rule_answer_alerts.sql') ON CONFLICT DO NOTHING;
COMMIT;
