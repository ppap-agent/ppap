BEGIN;

-- A rule clarification questionnaire: question cards found offline by clause-check on one version of a customer's
-- rules, and the customer's own answers. Nothing here is a formalization, a rule, a checker or an audit result,
-- and nothing reads it back into them; rules are still delivered offline by the platform.
CREATE TABLE IF NOT EXISTS product.rule_questionnaires (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    customer_id text NOT NULL CHECK (length(customer_id) BETWEEN 1 AND 64),
    rule_version text NOT NULL CHECK (length(rule_version) BETWEEN 1 AND 200),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    -- The import request as sent, so a replay of the same operation is recognised and a different one refused.
    import_digest text NOT NULL CHECK (import_digest ~ '^[a-f0-9]{64}$'),
    imported_by uuid NOT NULL REFERENCES product.users(id),
    imported_at timestamptz NOT NULL DEFAULT now(),
    closed_by uuid REFERENCES product.users(id),
    closed_at timestamptz,
    UNIQUE (tenant_id, customer_id, rule_version),
    CHECK ((closed_by IS NULL) = (closed_at IS NULL))
);

-- Cards are frozen at import: no runtime role may change or remove one.
CREATE TABLE IF NOT EXISTS product.rule_questions (
    questionnaire_id uuid NOT NULL REFERENCES product.rule_questionnaires(id),
    question_key text NOT NULL CHECK (length(question_key) BETWEEN 1 AND 200),
    position integer NOT NULL CHECK (position >= 0),
    card jsonb NOT NULL CHECK (jsonb_typeof(card) = 'object'),
    PRIMARY KEY (questionnaire_id, question_key),
    UNIQUE (questionnaire_id, position)
);

-- Every answer and every change to one is a new row: one answerer's current answer on a card is their latest
-- revision, and the earlier rows are its history. Several people may answer the same card; none is "final".
CREATE TABLE IF NOT EXISTS product.rule_question_answers (
    operation_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    questionnaire_id uuid NOT NULL,
    question_key text NOT NULL,
    answered_by uuid NOT NULL REFERENCES product.users(id),
    revision integer NOT NULL CHECK (revision > 0),
    choice text CHECK (choice IS NULL OR length(choice) BETWEEN 1 AND 64),
    answer_text text CHECK (answer_text IS NULL OR length(answer_text) BETWEEN 1 AND 4000),
    note text CHECK (note IS NULL OR length(note) BETWEEN 1 AND 2000),
    request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
    answered_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (questionnaire_id, question_key) REFERENCES product.rule_questions(questionnaire_id, question_key),
    UNIQUE (questionnaire_id, question_key, answered_by, revision),
    CHECK (choice IS NOT NULL OR answer_text IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS rule_question_answers_card ON product.rule_question_answers (questionnaire_id, question_key, answered_by, revision DESC);

REVOKE UPDATE, DELETE ON product.rule_questionnaires FROM ppap_api;
GRANT SELECT, INSERT ON product.rule_questionnaires TO ppap_api;
GRANT UPDATE (closed_by, closed_at) ON product.rule_questionnaires TO ppap_api;
REVOKE UPDATE, DELETE ON product.rule_questions, product.rule_question_answers FROM ppap_api;
GRANT SELECT, INSERT ON product.rule_questions, product.rule_question_answers TO ppap_api;
REVOKE ALL ON product.rule_questionnaires, product.rule_questions, product.rule_question_answers FROM ppap_worker;

INSERT INTO product.schema_migrations (version) VALUES ('0085_rule_questionnaires.sql') ON CONFLICT DO NOTHING;
COMMIT;
