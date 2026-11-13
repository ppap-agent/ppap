BEGIN;

-- Deep mode no longer writes programs: the reviewer reads the Suite program's results,
-- and its per-clause record (confirmed / corrected / rejected) is kept on the finding.
-- Generated revisions of earlier deep rounds stay in suite_revisions, named by each
-- round's frozen checker_execution coverage.
DROP TABLE IF EXISTS product.deep_round_programs;

INSERT INTO product.schema_migrations (version) VALUES ('0074_deep_review.sql') ON CONFLICT DO NOTHING;
COMMIT;
