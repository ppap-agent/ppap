BEGIN;

-- Publishing text-only rules must not claim a proof or executable-code consent.
-- This narrow exception accepts only the exact inert starter and no resources
-- module, including inherited paths. All executable revisions keep the old gate.
CREATE OR REPLACE FUNCTION product.suite_has_no_executable_checker(revision_id uuid)
RETURNS boolean LANGUAGE sql STABLE AS $$
    WITH RECURSIVE lineage AS (
        SELECT id, base_revision_id, overrides, 1 AS depth, ARRAY[id] AS visited
        FROM product.suite_revisions WHERE id = revision_id
        UNION ALL
        SELECT r.id, r.base_revision_id, r.overrides, l.depth + 1, l.visited || r.id
        FROM lineage l JOIN product.suite_revisions r ON r.id = l.base_revision_id
        WHERE l.depth < 16 AND NOT r.id = ANY(l.visited)
    ), paths AS (
        SELECT DISTINCT ON (resource.key) resource.key, resource.value
        FROM lineage l CROSS JOIN LATERAL jsonb_each_text(l.overrides) resource
        ORDER BY resource.key, l.depth
    )
    SELECT EXISTS (SELECT 1 FROM lineage WHERE base_revision_id IS NULL)
       AND coalesce((SELECT value = '594688f37841558f818bb24fff444671eb311d23a255126b98fd8b7a6f828fed'
                     FROM paths WHERE key = 'checker.py'), false)
       AND NOT EXISTS (SELECT 1 FROM paths WHERE key = 'resources.py' AND value IS NOT NULL);
$$;
ALTER TABLE product.suite_publications DROP CONSTRAINT IF EXISTS suite_publications_check1;
ALTER TABLE product.suite_publications DROP CONSTRAINT IF EXISTS suite_publications_execution_authorized;
ALTER TABLE product.suite_publications ADD CONSTRAINT suite_publications_execution_authorized
    CHECK (verification_status = 'verified' OR force_unverified OR product.suite_has_no_executable_checker(revision_id));

INSERT INTO product.schema_migrations(version) VALUES ('0050_custom_rule_suites.sql') ON CONFLICT DO NOTHING;
COMMIT;
