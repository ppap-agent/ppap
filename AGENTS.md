# PPAP 工作入口

本仓是最终产品运行仓。开始一项工作时，先读**当前 owning Issue、直接相关源码和测试**。只有改动产品语义时读 [Northstar](docs/NORTHSTAR.md) / [DATA_MODEL](docs/DATA_MODEL.md)；改架构或安全边界时再读对应文档；迁移任务按需读取 [INVENTORY](docs/INVENTORY.md) / [MIGRATION](docs/MIGRATION.md)。

## 默认资产使用规则

`ppap-agent` GitHub 组织中的代码、配置、文档、测试、样本，以及我们能够访问的远程环境，默认都是本项目可直接使用的授权资产，除非产品负责人以后明确排除某项资产。

这不改变运行时安全边界：Secret 不能泄露，租户/对象访问必须经过产品授权，真实外部写操作按其业务影响处理。

## Authority

- 产品目标：Northstar 与当前 owning Issue。
- 当前行为：本仓源码、数据库约束和测试。
- 迁移 baseline：只由对应 owning Issue 拥有。
- 配置：typed Settings、[`.env.dev`](.env.dev) / [`.env.production`](.env.production) 默认值与 [`.env.example`](.env.example)。
- 当前工作状态：GitHub Issues / 当前 PR。

Issue 是验收合同，不是实施顺序。允许一个 PR 同时推进多个 Issue；不要为了票的先后制造串行依赖。

## 必须保持

- 所有数据访问执行租户与对象授权。
- 审核轮次与执行尝试分离；显式重审才创建新轮次。
- 受理与入队保持原子边界，晚到 Worker 不能覆盖当前结果。
- 历史轮次引用明确的文件版本和规则版本。
- 机器结果与人工复核分别保存，未知/空结果不能变成 PASS。
- 外部非幂等操作遇到未知结果时先读回/对账，不盲目重试。

## 开发方式

优化目标是**尽快完成迁移和开发，让客户尽早使用**。

优先走最短正确路径：复用现成资产 → 迁入本仓 → 跑 owning Issue 定义的 baseline/当前测试 → 修新增 regression 或产品缺口 → 打通客户路径。

KISS 不等于把迁移人为切成最小碎片。如果一个内聚 package、测试集或配置整体迁入更快、更少上下文，就整体迁入，再删除无消费者部分。

不要新增 roadmap、CURRENT、Story/proof/evidence registry、manifest 或第二套状态台账。历史信息由 Git history 和已关闭 Issue 保存。

## 最小开发上下文（每次开发必遵守）

按 [context-reduce](https://github.com/ronhuafeng/skills-release/tree/main/catalog/engineering/context-reduce) 执行：只读本次 owning Issue、直接相关的源码、测试和仍生效的文档入口；非必要不读旧分支、历史设计、旧 Issue、agent 日志或迁移记录。复用当前权威，消除无当前消费者的代码、兼容层、额外概念、重复文档和对应测试；用小范围测试守住真实不变量，重要外部效应必须读回验证。

**Checker artifacts 不是 legacy code。** `audit/checkers/v2`、`v3`、`v4`、`v5` 等版本目录中的 Checker 源码和资源是独立版本化 artifacts；不能因为版本旧或正在迁移 v5 就删除、覆盖、合并这些目录或其不可变 Revision。`context-reduce` 的删除范围是已证实无当前消费者的旧执行框架、隐式 fallback、重复注册/映射、无效兼容层及其过期文档和测试；不以文件名或版本号直接判定 legacy。新版本 Checker 使用自身完整代码、显式绑定及 `checker_lib`，不隐式调用其他版本；对旧执行接口不新增历史兼容保证。开发中继续保留权限、冻结版本/原件/审计证据、未知不得 PASS 等安全与正确性边界。

开发命令见 [DEVELOPMENT](docs/DEVELOPMENT.md)，选择和运行测试时见 [tests/README](tests/README.md)。
