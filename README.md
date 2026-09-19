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
| 测试与迁移 parity | [Testing](docs/TESTING.md) |
| 安全边界 | [Security](docs/SECURITY.md) |
| 迁移来源 | [Inventory](docs/INVENTORY.md)、[Migration](docs/MIGRATION.md) |

当前实现事实以 `main` 上的源码和测试为准，不维护第二份状态台账。

主要运行路径：

```text
apps/web -> services/api -> PostgreSQL / object storage -> services/worker -> packages
```

## 剩余四个可验收结果

实施状态只在 GitHub Issues：

- [#3 迁移 PPAP 审核引擎与完整回归资产](https://github.com/ppap-agent/ppap/issues/3)：与固定 `ppap-agent` baseline 行为一致，不新增 migration regression。
- [#7 完成 PPAP 核心审核产品流程](https://github.com/ppap-agent/ppap/issues/7)：完整浏览器审核闭环 E2E 可完成。
- [#11 完成飞书 PPAP 迁移](https://github.com/ppap-agent/ppap/issues/11)：与现有飞书产品 baseline 相比，客户可见功能不减少。
- [#15 生产交付并 Sunset 旧 PPAP 路径](https://github.com/ppap-agent/ppap/issues/15)：单仓可 install/test/build/deploy/upgrade/operate，客户主路径切换完成，终态不依赖 `ppap-agent`。

这四个 Issue 表达验收结果，不表达严格实施顺序；实现和 PR 可以并行或同时满足多个 Issue。

## 迁移原则

`ppap-agent` GitHub 组织中的代码、配置、文档、测试、样本和我们可访问的远程环境，默认都是可直接用于本项目迁移、开发、测试和交付的授权资产，除非之后明确排除。

优先复用已有可工作能力。内聚模块整体迁入更快时可以整体迁入，再删除无消费者部分；不为了架构纯净重写，也不为迁移额外建设 evidence/proof 系统。
