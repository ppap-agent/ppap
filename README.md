# PPAP

把遗留的 PPAP 产品、渠道和审核能力收敛为一个客户可用、可持续维护的产品。

> 打开一次审核，就知道审了什么、结果是什么、原始依据在哪里、下一步做什么。

## 从这里开始

| 需要了解 | 当前入口 |
|---|---|
| 产品目标和术语 | [Northstar](docs/NORTHSTAR.md) |
| 代码与运行边界 | [Architecture](docs/ARCHITECTURE.md) |
| 数据不变量 | [Data model](docs/DATA_MODEL.md) |
| 开发、配置、运行 | [Development](docs/DEVELOPMENT.md)、[.env.dev](.env.dev)、[.env.production](.env.production)、[.env.example](.env.example) |
| 测试 | [Testing](docs/TESTING.md) |
| 安全边界 | [Security](docs/SECURITY.md) |
| 迁移来源 | [Inventory](docs/INVENTORY.md)、[Migration](docs/MIGRATION.md) |

当前实现事实以 `main` 上的源码和测试为准，不维护第二份状态台账。

主要运行路径：

```text
apps/web -> services/api -> PostgreSQL / object storage -> services/worker -> packages
```

## 当前四个验收结果

实施状态只在 GitHub Issues：

- [#3 审核引擎与回归资产迁移](https://github.com/ppap-agent/ppap/issues/3)
- [#7 核心审核产品流程](https://github.com/ppap-agent/ppap/issues/7)
- [#11 飞书 PPAP 迁移](https://github.com/ppap-agent/ppap/issues/11)
- [#15 生产交付与旧路径 Sunset](https://github.com/ppap-agent/ppap/issues/15)

这些 Issue 是验收合同，不是实施顺序。具体迁移原则、资产使用默认和开发约束见 [AGENTS.md](AGENTS.md)；不要在 README 复制 Issue 正文或迁移 baseline。
