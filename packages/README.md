# Packages

`packages/` 只放当前应用真正复用的库。包是库，不是隐藏微服务。

当前：

| Package | Owns |
|---|---|
| `ppap_core` | typed settings、数据库/对象存储基础能力、共享产品类型 |

来自 `ppap-agent` 的检查器、DocOps、PDF、OCR、Vision 等能力**不预先整包迁入**。#3 的原生 corpus test 先暴露当前失败，#6 再迁入让该失败用例正确通过所需的最小代码和依赖。

新增 package 必须有当前消费者和直接测试；没有真实需要时不为未来格式或 provider 预留 package/plugin 层。
