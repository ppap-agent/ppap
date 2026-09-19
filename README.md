# PPAP

把遗留的 PPAP 产品、渠道和审核能力尽快收敛为一个客户可用、可持续维护的产品。

> 打开一次审核，就知道审了什么、结果是什么、原始依据在哪里、下一步做什么。

## 从这里开始

| 需要了解 | 当前入口 |
|---|---|
| 产品目标和术语 | [Northstar](docs/NORTHSTAR.md) |
| 代码与运行边界 | [Architecture](docs/ARCHITECTURE.md) |
| 数据不变量 | [Data model](docs/DATA_MODEL.md) |
| 开发、配置、运行 | [Development](docs/DEVELOPMENT.md)、[.env.example](.env.example) |
| 测试 | [Testing](docs/TESTING.md) |
| 安全边界 | [Security](docs/SECURITY.md) |
| 当前工作 | [GitHub Issues](https://github.com/ppap-agent/ppap/issues) |
| 迁移来源 | [Inventory](docs/INVENTORY.md)、[Migration](docs/MIGRATION.md) |

当前实现事实以 `main` 上的源码和测试为准，不维护第二份状态台账。

主要运行路径：

```text
apps/web -> services/api -> PostgreSQL / object storage -> services/worker -> packages
```

## 当前迁移原则

`ppap-agent` GitHub 组织中的代码、配置、文档、测试、样本和我们可访问的远程环境，默认都是可直接用于本项目迁移、开发、测试和交付的授权资产，除非之后明确排除。

目标不是重新证明这些资产是否能用，而是**尽快把已有能力收敛进 `ppap` 并让客户使用**：

- 可工作的实现优先复用，不为了新架构重写。
- 测试、fixture、oracle、配置随能力一起迁入本仓。
- 内聚模块整体迁入更快时，可以整体迁入，再删除无消费者部分。
- `ppap-agent` 完成迁移后 sunset；终态 CI、开发、运行不依赖旧仓。
