-- Live 2026-11-24 SELECT on le_uac.th_feishu_volume_report: legacy free-trial points per Feishu tenant, before
-- opening current to every tenant. Coastline's own tenants are excluded. No pending/unknown paid rows existed.
INSERT INTO product.trial_cutover_baselines
    (provider, tenant_key, ai_item_id, consumed_points, source, recorded_at)
VALUES
    ('feishu', '1088406f1c155740', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 无锡车联天下智能科技股份有限公司', now()),
    ('feishu', '10c7e27a3043975e', 'OpenApp_Lcoastline_PPAPAuditAgent', 3500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 抗疫小分队', now()),
    ('feishu', '11209e25ae8e575d', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 飞书个人版', now()),
    ('feishu', '11c1ad08115b175e', 'OpenApp_Lcoastline_PPAPAuditAgent', 2500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 联合汽车电子有限公司', now()),
    ('feishu', '12495bc8fd8f1758', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 为沃科技', now()),
    ('feishu', '13d079debf0e575e', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 导远电子Asensing', now()),
    ('feishu', '1406548d3c95175d', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 万向钱潮股份公司', now()),
    ('feishu', '140ef9f84e589740', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 温州丰迪接插件有限公司', now()),
    ('feishu', '1449c2567acd1758', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 广东省金桥轻合金（科技）有限公司', now()),
    ('feishu', '14574493429a5758', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 广州安时科技有限公司', now()),
    ('feishu', '14a24c129d9d9758', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 温州丰迪接插件有限公司', now()),
    ('feishu', '14a5a337df9c574f', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 飞书个人版', now()),
    ('feishu', '1562e300a5145758', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; ADAYO华阳多媒体', now()),
    ('feishu', '1596eec77f94975f', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 飞书用户2689QU的组织', now()),
    ('feishu', '15b23e7faf14d740', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 陈文韵', now()),
    ('feishu', '15ffabcb65275758', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; isv测试企业1', now()),
    ('feishu', '1629d31db550975f', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 上海保隆汽车科技股份有限公司', now()),
    ('feishu', '172d29e9015f5758', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 互联网行业-演示租户', now()),
    ('feishu', '19032440e38c9be8', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; SBD', now()),
    ('feishu', '1956263aa26edc8a', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户214213的组织', now()),
    ('feishu', '197b2a7e26015c90', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 苏州测试公司', now()),
    ('feishu', '19b0466cf85d5a43', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户332288的组织', now()),
    ('feishu', '1a41c6e861879c98', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户047189的组织', now()),
    ('feishu', '1a5dde4100da5bbc', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 豆包账号', now()),
    ('feishu', '1aa57683c39f1b80', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 中信戴卡采供中心', now()),
    ('feishu', '1ab057259c821c81', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 英得尔实业（广东）股份有限公司', now()),
    ('feishu', '1bafd52d459a575d', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户195612的组织', now()),
    ('feishu', '1bce41abfcd3575f', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户785612的飞书', now()),
    ('feishu', '1bdb28185007d74f', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户787098的组织', now()),
    ('feishu', '1bdb2a5d82ed1740', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 用户566103的组织', now()),
    ('feishu', '2c8660868c4d975e', 'OpenApp_Lcoastline_PPAPAuditAgent', 500, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 飞创', now()),
    ('feishu', '2e0d30ebca4f1651', 'OpenApp_Lcoastline_PPAPAuditAgent', 1000, 'legacy le_uac.th_feishu_volume_report 2026-11-24; 远峰科技股份有限公司', now())
ON CONFLICT (provider, tenant_key, ai_item_id) DO UPDATE
SET consumed_points = EXCLUDED.consumed_points,
    source = EXCLUDED.source,
    recorded_at = EXCLUDED.recorded_at;
INSERT INTO product.schema_migrations (version) VALUES ('0098_trial_cutover_baseline_all.sql') ON CONFLICT DO NOTHING;
