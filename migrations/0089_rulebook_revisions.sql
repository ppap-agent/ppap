BEGIN;

-- A revision of one customer's rulebook, in one enterprise. Its clauses are rows of product.rule_versions (one per
-- document type) that name it; those rows are never candidates for an audit. A revision is written once: only "this
-- revision is clarified" is recorded on it later. `changes` says, per clause, how it differs from the revision it
-- starts from and on what grounds (question cards and their answers).
CREATE TABLE IF NOT EXISTS product.rulebook_revisions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants(id),
    customer_id text NOT NULL CHECK (length(customer_id) BETWEEN 1 AND 64),
    version text NOT NULL CHECK (version ~ '^v[0-9]+\.[0-9]+$'),
    revision integer NOT NULL CHECK (revision >= 0),
    base_version text CHECK (base_version IS NULL OR base_version ~ '^v[0-9]+\.[0-9]+$'),
    base_revision integer CHECK (base_revision IS NULL OR base_revision >= 0),
    changes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(changes) = 'object'),
    origin text NOT NULL CHECK (origin IN ('shipped', 'local', 'clarification')),
    digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
    created_by uuid REFERENCES product.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    clarified_by uuid REFERENCES product.users(id),
    clarified_at timestamptz,
    UNIQUE (tenant_id, customer_id, version, revision),
    CHECK ((base_version IS NULL) = (base_revision IS NULL)),
    CHECK ((clarified_by IS NULL) = (clarified_at IS NULL))
);

ALTER TABLE product.rule_versions ADD COLUMN IF NOT EXISTS rulebook_revision_id uuid REFERENCES product.rulebook_revisions(id);
CREATE INDEX IF NOT EXISTS rule_versions_rulebook_revision ON product.rule_versions (rulebook_revision_id) WHERE rulebook_revision_id IS NOT NULL;

-- What a clause checks and when it passes, fails or goes to a person: written beside a clarified clause, never a new
-- revision of it. One reading per clause, written once.
CREATE TABLE IF NOT EXISTS product.rule_clause_readings (
    revision_id uuid NOT NULL REFERENCES product.rulebook_revisions(id),
    clause_id text NOT NULL CHECK (length(clause_id) BETWEEN 1 AND 64),
    reading jsonb NOT NULL CHECK (jsonb_typeof(reading) = 'object'),
    confirmed_by uuid NOT NULL REFERENCES product.users(id),
    confirmed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (revision_id, clause_id)
);

GRANT SELECT, INSERT ON product.rulebook_revisions, product.rule_clause_readings TO ppap_api;
-- Who marked it clarified is the action that did (0093); the revision keeps only when.
GRANT UPDATE (clarified_at) ON product.rulebook_revisions TO ppap_api;
REVOKE ALL ON product.rulebook_revisions, product.rule_clause_readings FROM ppap_worker;

-- The clarification loop sends cards to an open questionnaire and closes the questionnaire of a superseded revision.
-- A card's content stays as written; answers are still append-only.
GRANT INSERT ON product.rule_questionnaires, product.rule_questions TO ppap_api;
GRANT UPDATE (closed_at) ON product.rule_questionnaires TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0089_rulebook_revisions.sql') ON CONFLICT DO NOTHING;
COMMIT;
