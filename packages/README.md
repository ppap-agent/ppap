# Packages

`packages/` 只放当前应用真正复用的库。包是库，不是隐藏微服务。

当前：

| Package | Owns |
|---|---|
| `ppap_core` | typed settings、数据库/对象存储基础能力、共享产品类型 |

`ppap-agent` 是待 sunset 的迁移来源，不是 package dependency。#3 先把仍有价值的回归测试、fixture、oracle 和必要 test helper 迁入本仓；这些本仓测试暴露真实失败后，#6 再迁入让失败用例正确通过所需的最小检查/解析代码。

新增 package 必须有本仓当前消费者和直接测试。没有真实失败或当前需求时，不为未来格式、OCR/provider 或 Agent/workflow 预留 package/plugin 层。迁入一个能力后，不保留指向 `ppap-agent` 的跨仓 import、运行时源码路径或 CI checkout。
