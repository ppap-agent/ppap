# PPAP 工作入口

本仓是最终产品运行仓。开始一项工作时，先读**当前 owning Issue、直接相关源码和测试**。只有改动产品语义时读 [Northstar](docs/NORTHSTAR.md) / [DATA_MODEL](docs/DATA_MODEL.md)；改架构或安全边界时再读对应文档；迁移任务按需读取 [INVENTORY](docs/INVENTORY.md) / [MIGRATION](docs/MIGRATION.md)。

## 默认资产权限

`ppap-agent` GitHub 组织中的代码、配置、文档、测试、样本，以及我们能够访问的远程环境，默认都是本项目可直接使用的授权资产，除非产品负责人以后明确排除某项资产。

Secret 仍按安全边界处理，租户/对象访问仍按产品授权执行；“资产可用于项目”不等于可以泄露凭据或绕过业务权限。

## 当前四个结果

剩余产品工作只由 #3、#7、#11、#15 四个大 Issue 拥有。Issue 是**验收合同，不是实施顺序**：

- #3：engine/corpus 与固定旧系统 baseline 行为 parity。
- #7：完整 Web/PWA 审核闭环 E2E。
- #11：飞书现有客户功能 parity，不减少。
- #15：生产级单仓交付并 sunset 旧 PPAP 路径。

允许一个 PR 同时推进或完成多个 Issue；不要为了票的顺序制造串行依赖。

## 权威

- 产品目标：Northstar 与当前 owning Issue。
- 当前行为：本仓源码、数据库约束和测试。
- 迁移下限：对应 owning Issue 写明的固定 legacy baseline。
- 配置：typed Settings 与 [`.env.example`](.env.example)。
- 当前工作状态：GitHub Issues / 当前 PR。

旧仓和远程环境是可利用的迁移/开发资产，但不是终态 runtime authority。`ppap-agent` 完成迁移后 sunset。

## 必须保持

- 所有数据访问执行租户与对象授权。
- 审核轮次与执行尝试分离；显式重审才创建新轮次。
- 受理与入队保持原子边界，晚到 Worker 不能覆盖当前结果。
- 历史轮次引用明确的文件版本和规则版本。
- 机器结果与人工复核分别保存，未知/空结果不能变成 PASS。
- 外部非幂等操作遇到未知结果时先读回/对账，不盲目重试。

## 开发方式

优化目标是**尽快完成迁移和开发，让客户尽早使用**。

优先走最短正确路径：复用现成资产 → 迁入本仓 → 跑 baseline/当前测试 → 修 migration regression 或产品缺口 → 打通客户路径。

KISS 不等于人为把迁移切成最小碎片。如果一个内聚 package、测试集或配置整体迁入更快、更少上下文，就整体迁入，再删除无消费者部分。不要新增 roadmap、proof registry、evidence manifest 或第二套状态台账。

开发命令见 [DEVELOPMENT](docs/DEVELOPMENT.md)，测试策略见 [TESTING](docs/TESTING.md)。
