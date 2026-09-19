# Packages

`packages/` 只放当前 PPAP 实际使用的库。包是库，不是隐藏微服务。

当前：

| Package | Owns |
|---|---|
| `ppap_core` | typed settings、数据库/对象存储基础能力、共享产品类型 |

#3 负责把 `ppap-agent@53a5bd2b00adaf7332602bbdbe3cfa82817ebbb9` 中当前 PPAP 需要的检查能力和完整 regression 资产迁入本仓。

迁移 package 时以**最快达到 baseline parity 并降低终态上下文**为准：
- 若现有 package 已经内聚、可工作且 PPAP 当前需要，整体迁入通常比逐文件重写更合理；
- pytest、fixture、oracle、配置和实现一起迁入，迁后由本仓维护；
- baseline 本来存在的算法 failure 不要求在迁移中修复；
- 迁入后删除无消费者代码、跨仓 import、源码路径和 CI checkout；
- 不为未来可能的格式/provider/Agent 平台预建额外抽象。

KISS 的目标是最终系统更少上下文，不是要求迁移过程必须最小粒度。
