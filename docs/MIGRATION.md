# 迁移与退出

本文件只在 legacy migration 期间存在。迁移 baseline 的具体 Git revision 只由 owning Issue 保存：
- engine/corpus：#3
- 飞书产品：#11
- 最终切换 / sunset：#15

#15 完成后，本文件应删除或缩减到仍存在的真实兼容边界；历史迁移设计由 Git history 保存。

## 原则

- 组织内代码、配置、文档、测试、样本和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。
- Engine 迁移保持 #3 baseline 行为，不要求修复旧系统本来存在的算法 failure。
- 飞书迁移保持 #11 定义的现有客户 capability 不减少；UI 可以重构，不要求像素一致。
- 来源仓只作为迁移输入；有长期价值的代码、tests、fixture、oracle、配置和 helper 迁入本仓。
- 一个内聚模块整体迁入更快时可以整体迁入；迁后删除跨仓 import、checkout、旧 HTTP wrapper 和无消费者代码。
- Issue 之间表达验收关系，不规定实施顺序。

## 当前试用期切换

当前客户为试用阶段，旧审核历史不是上线前置。文件和规则可以在新系统重新导入，因此不建设通用历史数据迁移框架。

未来某个具体客户明确要求旧历史时，再针对该客户和实际数据处理。

## Sunset

当 #3/#7/#11 的客户能力成立，并且 #15 的单仓 build/deploy/operate 成立后，切换客户入口；`ppap-agent` 不再承担当前运行责任即可 archive/sunset。

其它共享 Java/客户系统只有确有其它消费者的部分继续保留。
