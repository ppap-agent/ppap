-- Explicit report recovery requeues the existing report, never its audit.
GRANT UPDATE (payload, status, result, lease_owner, lease_until, updated_at)
    ON engine.jobs TO ppap_api;
INSERT INTO product.schema_migrations (version) VALUES ('0026_report_recovery.sql') ON CONFLICT DO NOTHING;
