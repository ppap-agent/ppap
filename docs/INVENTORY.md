# 迁移来源清单

本文件只在 legacy migration 期间帮助快速找到现成资产，不拥有迁移验收 baseline，也不是权限审批清单。资产使用默认见 [AGENTS.md](../AGENTS.md)；具体 baseline 见 #3 / #11。

| 能力 | 可利用来源 | Owning outcome |
|---|---|---|
| 审核引擎、规则、测试、DocOps/PDF/OCR/Vision | `ppap-agent` | #3 |
| 飞书前端 | `ppap-web` | #11 |
| 飞书身份、提交、用量 | `ppap-uac` | #11 |
| Java PPAP/file-audit/transformer | `ppap-biz` | #11 |
| PC Web UI prior art | `ppap-host` | #57（current 入口在 `apps/web`）/ #58 |
| 产品行为与现网运行知识 | `hax` | #11 / #15 |
| 当前远程环境、配置和数据源 | 组织内可访问环境 | 对应 owning Issue |

当前不建设通用历史审核迁移：试用用户可重新导入文件和规则；未来有具体客户需求时按实际数据处理。

迁入后本仓拥有当前代码、测试、配置和运行入口；来源仓只保留历史价值或其它非 PPAP 消费者。

#15 完成并 sunset legacy PPAP 后，本文件应删除或只保留仍存在的真实外部兼容边界。
