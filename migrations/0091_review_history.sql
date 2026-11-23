BEGIN;

-- A human review is kept as every version it ever had: a correction adds a row, it never rewrites one (#358).
-- product.reviews stays a table (earlier migrations still alter it and hang task events on it); one row per version.
-- What a review says now is product.current_reviews, read from those rows, never stored a second time.

-- The severity (A/B/C) a reviewer gave this item; NULL when no reviewer changed it.
ALTER TABLE product.reviews ADD COLUMN IF NOT EXISTS severity text;
ALTER TABLE product.reviews DROP CONSTRAINT IF EXISTS reviews_severity_known;
ALTER TABLE product.reviews ADD CONSTRAINT reviews_severity_known CHECK (severity IS NULL OR severity IN ('A', 'B', 'C'));

DO $$
BEGIN
    -- From one row per item to one row per version; existing rows become the version they already carry.
    IF (SELECT count(*) FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'product.reviews'::regclass AND i.indisprimary) = 2 THEN
        ALTER TABLE product.reviews DROP CONSTRAINT reviews_pkey;
        ALTER TABLE product.reviews ADD CONSTRAINT reviews_pkey PRIMARY KEY (round_id, item_key, version);
    END IF;
END $$;

CREATE OR REPLACE VIEW product.current_reviews AS
    SELECT DISTINCT ON (round_id, item_key) round_id, item_key, user_id, judgment, rationale, next_step, version, updated_at, citations, severity
    FROM product.reviews
    ORDER BY round_id, item_key, version DESC;

GRANT SELECT ON product.current_reviews TO ppap_api;
-- Versions are only ever added.
REVOKE UPDATE, DELETE ON product.reviews FROM ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0091_review_history.sql') ON CONFLICT DO NOTHING;
COMMIT;
