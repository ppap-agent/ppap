# PPAP

把两家公司遗留的产品、渠道和审核能力整合成一个可追溯的 PPAP 产品。

> 打开一次审核，就知道审了什么、依据在哪里、结果是什么、下一步做什么。

**当前状态：目标产品尚未实现。** 本仓已建立接管文档、真实 Issue 路线图和首批可执行验收工具。工具自测、依赖探针或 Story 文本都不等于新产品验收通过。

## 从这里开始

| 问题 | 唯一入口 |
|---|---|
| 产品承诺、术语与完成范围 | [Northstar](docs/NORTHSTAR.md) |
| 代码在哪里、责任怎样分 | [架构](docs/ARCHITECTURE.md) |
| 调查到了什么，什么仍未知 | [当前事实与来源](docs/CURRENT.md) |
| 旧能力如何迁移 | [能力与数据归属](docs/INVENTORY.md)、[迁移原则](docs/MIGRATION.md) |
| 为什么这样选技术 | [决策](docs/DECISIONS.md)、[外部方案比较](docs/RESEARCH.md) |
| 下一步做什么 | [Roadmap](docs/ROADMAP.md) 导航到真实 Issues；Issues 拥有实施状态 |
| 怎样开发、配置与运行 | [开发指南](docs/DEVELOPMENT.md)、[配置示例](.env.example) |
| 怎样证明用户结果 | [Live Stories](docs/STORIES.md)、[测试范围与命令](docs/TESTING.md) |
| 历史结果怎样可信 | [数据约束](docs/DATA_MODEL.md) |
| 密钥、授权与敏感数据 | [安全边界](docs/SECURITY.md) |

终态是 `apps/web → services/api → PostgreSQL / 对象存储 → services/worker`。`packages` 是可复用库，`migrations` 管数据库变更，`deploy` 管运行交付，`tests` 管证明。Worker 不向浏览器暴露第二套产品 API。

Core LLM 固定走 Responses-compatible API。当前 isolated-dev profile 默认 `LLM_BASE_URL=https://api.trustedtunnel.app/v1`、`LLM_MODEL=gpt-5.6-luna`，因此 Cursor 日常只需要 `LLM_API_KEY` 一个 core Secret；两项默认均可被环境覆盖。LLM 与飞书环境可用性分别由两个独立 reusable GitHub Actions probe 用 `curl + jq` 验证；每次 push 的 `Push preflight` 必须等两者成功后才继续 deterministic verification。不能把 readiness 成功当作产品验收成功。

隔离开发的 PostgreSQL/对象存储使用仓库 defaults；飞书、Partner AI 和 legacy migration 都按 capability 接入，不是 core startup 前置。

`ppap-agent/ppap-agent` 是当前引擎与迁移来源，不是终态跨仓依赖。整合不等于立即关闭来源服务：客户安装、其它产品与旧链接须分别核验后退出。

本仓保持私有。采用高质量开源工程标准，不代表已取得公开遗留代码、规则、样本或客户资料的许可。
