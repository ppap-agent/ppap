CREATE TABLE IF NOT EXISTS product.trial_cutover_baselines (
    provider text NOT NULL,
    tenant_key text NOT NULL,
    ai_item_id text NOT NULL,
    consumed_points integer NOT NULL CHECK (consumed_points >= 0),
    source text NOT NULL,
    recorded_at timestamptz NOT NULL,
    PRIMARY KEY (provider, tenant_key, ai_item_id)
);
-- Live 2026-09-22 SELECT on le_uac.th_feishu_volume_report for 万向/保隆/联电.
-- Trial rows only; paid Partner AI stays on Feishu quota, not this ledger.
INSERT INTO product.trial_cutover_baselines
    (provider, tenant_key, ai_item_id, consumed_points, source, recorded_at)
VALUES
    ('feishu', '1406548d3c95175d', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000,
     'legacy le_uac.th_feishu_volume_report 2026-09-22; 万向钱潮股份公司; no pending/unknown paid rows', now()),
    ('feishu', '1629d31db550975f', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000,
     'legacy le_uac.th_feishu_volume_report 2026-09-22; 上海保隆汽车科技股份有限公司; no pending/unknown paid rows', now()),
    ('feishu', '11c1ad08115b175e', 'OpenApp_Lcoastline_PPAPAuditAgent', 2500,
     'legacy le_uac.th_feishu_volume_report 2026-09-22; 联合汽车电子有限公司; no pending/unknown paid rows', now())
ON CONFLICT (provider, tenant_key, ai_item_id) DO UPDATE
SET consumed_points = EXCLUDED.consumed_points,
    source = EXCLUDED.source,
    recorded_at = EXCLUDED.recorded_at;
INSERT INTO product.schema_migrations (version) VALUES ('0018_trial_cutover_baseline.sql') ON CONFLICT DO NOTHING;
