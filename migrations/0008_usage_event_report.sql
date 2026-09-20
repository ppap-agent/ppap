GRANT UPDATE ON product.usage_events TO ppap_api;

INSERT INTO product.schema_migrations (version) VALUES ('0008_usage_event_report.sql')
ON CONFLICT DO NOTHING;
