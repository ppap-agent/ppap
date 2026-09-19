# 迁移与退出

目标是尽快把已有 PPAP 能力收敛进本仓并让客户使用。

## Baselines

- Engine/corpus：`ppap-agent@53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9`
- Feishu frontend：`ppap-web@557a2aeab3e5f5e68d8e8cf47593ca172da733d7`
- UAC：`ppap-uac@fbe912faf1894fe018ea88c20e46a57842468623`
- Java PPAP：`ppap-biz@7570d966f178cde9c841600be485c43109d92019`
- Host slice：`ppap-host@c32b46a0a0d4f252a1677e8abce53a210b3c6d28`
- 产品上下文：`hax@0923f5acea380ddd09813621703777153a8c7971`

Git commit 本身就是 baseline identity，不建立第二套 manifest。

## 迁移原则

- 组织内代码、配置、文档、测试、样本和可访问环境默认可直接使用。
- Engine 迁移保持固定 baseline 行为，不要求修复旧系统本来存在的算法 failure。
- 飞书迁移保持现有客户 capability 不减少；UI 可以现代化，不要求像素一致。
- 来源仓只作为迁移输入；有长期价值的代码、tests、fixture、oracle、配置和 helper 迁入本仓。
- 一个内聚模块整体迁入更快时可以整体迁入；迁后持续删除跨仓 import、checkout、旧 HTTP wrapper 和无消费者代码。
- Issue 之间表达验收关系，不规定实施顺序。

## 当前试用期切换

当前客户为试用阶段，旧审核历史不是上线前置。文件和规则可以在新系统重新导入，因此不建设通用历史数据迁移框架。

如果未来具体客户明确要求迁旧历史，再针对该客户和实际数据处理。

## Sunset

当 #3/#7/#11 的客户能力在新系统成立，并且 #15 的单仓 build/deploy/operate 成立后，切换客户入口；`ppap-agent` 不再承担当前运行责任即可 archive/sunset。

其它共享 Java/客户系统只有确有其它消费者的部分继续保留。
