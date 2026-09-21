# 迁移与退出

本文件只在 legacy migration 期间存在。迁移 baseline 的具体 Git revision 只由 owning Issue 保存：
- engine/corpus：#3
- 飞书产品：#11
- PC UI：#57
- InsForge runtime：#58
- 本仓生产交付 / 客户入口切换：#15

#15 完成后，本文件应删除或缩减到仍存在的真实兼容边界；历史迁移设计由 Git history 保存。

## 原则

- 组织内代码、配置、文档、测试、样本和可访问环境按 [AGENTS.md](../AGENTS.md) 的默认规则直接使用。
- Engine 迁移保持 #3 baseline 行为，不要求修复旧系统本来存在的算法 failure。
- 飞书迁移保持 #11 定义的现有客户 capability 不减少；UI 可以重构，不要求像素一致。
- 来源仓可作为迁移输入；`ppap-agent` 迁入后仍是活的参考实现，不是一次性来源。
- 一个内聚模块整体迁入更快时可以整体迁入；迁后删除跨仓 import、checkout、旧 HTTP wrapper。不要为了“迁完”而删除仍有其它用户或算法验证价值的 `ppap-agent`。
- Issue 之间表达验收关系，不规定实施顺序。

## 当前试用期切换

当前客户为试用阶段，旧审核历史不是上线前置。文件和规则可以在新系统重新导入，因此不建设通用历史数据迁移框架。

未来某个具体客户明确要求旧历史时，再针对该客户和实际数据处理。

## 客户入口与 `ppap-agent`

当 #3/#7/#11 的客户能力成立，并且 #15 的单仓 build/deploy/operate 成立后，可以把**本仓客户入口**切到 current `ppap`。这不结束 `ppap-agent`：它继续服务其它用户，并作为新算法的验证基地和参考实现。不要 archive 或拆除该仓。

其它共享 Java/客户系统只有确有其它消费者的部分继续保留。
