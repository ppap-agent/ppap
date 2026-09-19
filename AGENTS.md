# PPAP 工作入口

本仓是最终产品运行仓。开始一项工作时，先读**当前 Issue、直接相关源码和测试**。只有改动产品语义时读 [Northstar](docs/NORTHSTAR.md) / [DATA_MODEL](docs/DATA_MODEL.md)；改架构或安全边界时再读对应文档；只有迁移任务才读 [INVENTORY](docs/INVENTORY.md) / [MIGRATION](docs/MIGRATION.md)。

## 权威

- 产品目标：Northstar 与当前 owning Issue。
- 当前行为：本仓源码、数据库约束和测试。
- 配置：typed Settings 与 [`.env.example`](.env.example)。
- 当前工作状态：GitHub Issues / 当前 PR。
- 历史调查和旧实现留在 Git history、已关闭 Issue 和来源仓，不作为普通开发必读。

`ppap-agent` 是待 sunset 的一次性迁移来源，不是当前 authority。不要新增对它的 runtime、CI、test、fixture、oracle 或开发期 checkout 依赖。仍有产品价值的代码和测试资产应迁入本仓后由本仓维护。

一个事实只保留一个当前 owner。不要为已有 authority 再建立 roadmap、evidence registry、manifest、兼容包装层或第二套状态台账。

## 必须保持

- 所有数据访问执行租户与对象授权。
- 审核轮次与执行尝试分离；显式重审才创建新轮次。
- 受理与入队保持原子边界，晚到 Worker 不能覆盖当前结果。
- 历史轮次引用明确的文件版本和规则版本。
- 机器结果与人工复核分别保存，未知/空结果不能变成 PASS。
- 外部非幂等操作遇到未知结果时先读回/对账，不盲目重试。

## 开发方式

优先走最短正确路径：修改局部源码 → 跑最近的 unit/integration test → 必要时跑一个真实外部边界测试。没有当前消费者或当前失败，不增加抽象、配置、gate 或服务。

外部 LLM、飞书、Partner AI 等按 capability 使用；它们的临时不可用不能阻塞无关 Core 测试。凭据只进入 gitignored 本地配置或运行时 Secret，日志不保留 token、cookie 或客户原文。

开发命令见 [DEVELOPMENT](docs/DEVELOPMENT.md)，测试策略见 [TESTING](docs/TESTING.md)。
