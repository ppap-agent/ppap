# Packages

`packages/` 只放当前 PPAP 实际使用的库。包是库，不是隐藏微服务。

当前：

| Package | Owns |
|---|---|
| `ppap_core` | typed settings、数据库/对象存储基础能力、共享产品类型 |

`ppap-agent` 和组织内其它仓库是可直接利用的授权迁移来源。

迁移 package 时以**最快完成客户路径并降低终态上下文**为准：
- 若一个现有 package 已经内聚、可工作，并且 PPAP 当前需要，整体迁入通常比逐文件重写更合理；
- pytest、fixture、oracle、配置和实现一起迁入，迁后由本仓维护；
- 迁入后删除无消费者代码、跨仓 import、源码路径和 CI checkout；
- 不为未来可能的格式/provider/Agent 平台预建额外抽象。

KISS 的目标是让最终系统更少上下文，不是要求迁移过程必须最小粒度。
