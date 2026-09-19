# PPAP 工作入口

本仓是最终产品运行仓。开始一项工作时，先读**当前 Issue、直接相关源码和测试**。只有改动产品语义时读 [Northstar](docs/NORTHSTAR.md) / [DATA_MODEL](docs/DATA_MODEL.md)；改架构或安全边界时再读对应文档；迁移任务按需读取 [INVENTORY](docs/INVENTORY.md) / [MIGRATION](docs/MIGRATION.md)。

## 默认资产权限

`ppap-agent` GitHub 组织中的代码、配置、文档、测试、样本，以及我们能够访问的远程环境，默认都是本项目可直接使用的授权资产，除非产品负责人以后明确排除某项资产。

因此：
- 不因为“旧仓”“业务样本”“远程环境”或“客户历史系统”等标签停工等待额外许可证明。
- 能直接复用的现有实现优先复用；能直接迁入的测试、fixture、oracle、配置和工具优先迁入。
- Secret 仍按安全边界处理，租户/对象访问仍按产品授权执行；“资产可用于项目”不等于可以泄露凭据或绕过业务权限。

## 权威

- 产品目标：Northstar 与当前 owning Issue。
- 当前行为：本仓源码、数据库约束和测试。
- 配置：typed Settings 与 [`.env.example`](.env.example)。
- 当前工作状态：GitHub Issues / 当前 PR。
- 旧仓和远程环境是可利用的迁移/开发资产，但不是终态 runtime authority。

`ppap-agent` 是待 sunset 的迁移来源。不要新增长期 runtime、CI、test、fixture、oracle 或开发期 checkout 依赖；有价值资产迁入本仓后由本仓维护。

## 必须保持

- 所有数据访问执行租户与对象授权。
- 审核轮次与执行尝试分离；显式重审才创建新轮次。
- 受理与入队保持原子边界，晚到 Worker 不能覆盖当前结果。
- 历史轮次引用明确的文件版本和规则版本。
- 机器结果与人工复核分别保存，未知/空结果不能变成 PASS。
- 外部非幂等操作遇到未知结果时先读回/对账，不盲目重试。

## 开发方式

优化目标是**尽快完成迁移和开发，让客户尽早使用**。

优先走最短正确路径：直接复用可工作的现有资产 → 迁入本仓 → 跑最近的测试 → 修真实失败 → 尽快打通客户路径。

KISS 不等于人为把迁移切成最小碎片。如果一个内聚 package、测试集或配置整体迁入更快、更少上下文，就整体迁入，再删除无消费者部分。没有当前消费者或当前失败时，不新增抽象、配置、gate 或服务。

外部 LLM、飞书、Partner AI 和其它可访问环境可以直接用于相关开发/验证；它们的临时不可用不能阻塞无关 Core 工作。

开发命令见 [DEVELOPMENT](docs/DEVELOPMENT.md)，测试策略见 [TESTING](docs/TESTING.md)。
