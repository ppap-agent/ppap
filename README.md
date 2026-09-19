# PPAP

把遗留的 PPAP 产品、渠道和审核能力收敛为一个可维护的产品。

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
| 仅迁移任务需要的来源信息 | [Inventory](docs/INVENTORY.md)、[Migration](docs/MIGRATION.md) |

当前实现事实以 `main` 上的源码和测试为准，不维护另一份“当前状态”文档。

主要运行路径是：

```text
apps/web -> services/api -> PostgreSQL / object storage -> services/worker -> packages
```

`packages` 是可复用库，不再包装成第二个产品 HTTP 层。飞书、Partner AI、外部 LLM 等按 capability 接入；无关的可选集成不可用时，不阻塞 Core 开发和测试。

`ppap-agent/ppap-agent` 等来源仓只在当前迁移/回归任务需要时读取。来源服务是否退役由真实消费者决定，不由仓库名字或旧架构推断。
