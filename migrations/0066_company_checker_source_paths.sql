BEGIN;

-- Predefined checker leaves moved from checkers/v5/<customer>/<group> to checkers/<company>/<group>,
-- with ASCII company directories (uaes, li, nio). The stored path is the leaf's location, not part
-- of its content digest; only that column changes.
ALTER TABLE product.suite_revisions DISABLE TRIGGER immutable_suite_revision;
WITH moved AS (
    SELECT id, regexp_replace(source_path, '^v[0-9]+/', '') AS path
      FROM product.suite_revisions
     WHERE tenant_id IS NULL
       AND (source_path ~ '^v[0-9]+/[^/]+/[^/]+$' OR source_path ~ '^(UAES|理想|蔚来)/[^/]+$')
)
UPDATE product.suite_revisions r
   SET source_path = CASE split_part(m.path, '/', 1)
                         WHEN 'UAES' THEN 'uaes' WHEN '理想' THEN 'li' WHEN '蔚来' THEN 'nio'
                         ELSE split_part(m.path, '/', 1)
                     END || '/' || split_part(m.path, '/', 2)
  FROM moved m
 WHERE r.id = m.id;
ALTER TABLE product.suite_revisions ENABLE TRIGGER immutable_suite_revision;

INSERT INTO product.schema_migrations (version) VALUES ('0066_company_checker_source_paths.sql') ON CONFLICT DO NOTHING;
COMMIT;
