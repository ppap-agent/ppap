BEGIN;

-- A person's decision to let a Feishu Project work item's flow pass the PPAP
-- node although its latest round did not conform. It is kept apart from the
-- machine result and from reviews: the round's conclusion is never changed.
-- One release per round; a later round needs its own. The API writes it only
-- after reading the round with the person's own access.
CREATE TABLE IF NOT EXISTS product.meego_node_releases (
    round_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES product.tenants (id),
    object_ref text NOT NULL,
    released_by uuid NOT NULL REFERENCES product.users (id),
    released_by_name text NOT NULL DEFAULT '',
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT ON product.meego_node_releases TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0080_meego_node_releases.sql') ON CONFLICT DO NOTHING;
COMMIT;
