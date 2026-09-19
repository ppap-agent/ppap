# Packages

`packages/` 只放当前 PPAP 实际使用的可复用库。包是库，不是隐藏微服务。

当前：

| Package | Owns |
|---|---|
| `ppap_core` | typed settings、数据库/对象存储基础能力、共享产品类型 |

审核引擎和完整 regression 资产的迁移合同由 #3 拥有；具体 legacy baseline 不在本文件重复。

迁移 package 时以**最快达到 #3 parity 并降低终态上下文**为准：

- 现有 package 已经内聚、可工作且 PPAP 当前需要时，可以整体迁入；
- pytest、fixture、oracle、配置和实现随能力一起迁入并由本仓维护；
- legacy baseline 本来存在的算法 failure 不要求在迁移中修复；
- 迁入后删除无消费者代码、跨仓 import、源码路径和 CI checkout；
- 不为未来可能的格式/provider/Agent 平台预建额外抽象。

KISS 的目标是最终系统更少上下文，不是要求迁移过程必须最小粒度。
