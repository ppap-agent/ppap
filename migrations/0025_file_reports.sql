-- Initial file PDFs use the existing persistent queue and attempt fencing.
CREATE UNIQUE INDEX IF NOT EXISTS initial_file_report_unique
    ON engine.jobs (audit_round_id, (payload->>'source_file_version_id'), (payload->>'result_attempt'))
    WHERE kind = 'report_pdf';
GRANT SELECT (id, task_fingerprint) ON product.audit_drafts TO ppap_worker;
INSERT INTO product.schema_migrations (version) VALUES ('0025_file_reports.sql') ON CONFLICT DO NOTHING;
