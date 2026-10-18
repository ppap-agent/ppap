-- Preserve existing tickets without inventing their provider event time.
ALTER TABLE product.app_tickets ADD COLUMN IF NOT EXISTS provider_event_ts numeric;
INSERT INTO product.schema_migrations (version) VALUES ('0033_feishu_ticket_order.sql') ON CONFLICT DO NOTHING;
