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
| 测试 | [测试入口](tests/README.md) |
| 安全边界 | [Security](docs/SECURITY.md) |

当前实现事实以 `main` 上的源码和测试为准；工作状态只在 GitHub Issues，不维护第二份状态台账。具体开发约束见 [AGENTS.md](AGENTS.md)。

主要运行路径：

```text
apps/web -> apps/api -> PostgreSQL / object storage -> apps/worker -> packages
```

审核规则的条款形式化合同见 [DATA_MODEL](docs/DATA_MODEL.md)。模型语义质量未验收。
