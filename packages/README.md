# Packages

`packages/` 只放当前 PPAP 实际使用的可复用库。包是库，不是隐藏微服务。

当前：

| Package | Owns |
|---|---|
| `product` | 跨 core / engine / store 的 application orchestration 与 composition boundaries |
| `product` | 产品状态：文件、规则、轮次、配置、存储 |
| `audit` | 审核内核：规则、逻辑文档、证据、求值、手写检查 |
| `corpus_contracts` | Corpus regression domain（ExecutionOutcome、baseline、oracle reconciliation）；仅开发/测试 authority，不是产品 runtime API |
| `agent` | 独立 Deep 执行协议、UDS client、Controller；把冻结 round 转成 bounded spec 再写回 findings。`python -m agent` 是 Controller 进程，拥有 gVisor guest；Worker 只连 UDS |
| `documents` | 物理文档读取、定位和局部呈现。Excel、Word、PDF、图像、OCR 与 PDF 转换都在这个包内，不判定审核结果 |
| `audit.engine` | 规则检查器与 worker audit runtime |
| `product.store` | API / Worker 共用的 product PostgreSQL persistence implementation |

Worker 调用 `audit.engine.app_runtime.run_audit_engine`。#11 飞书 parity 仍由对应 Issue 拥有。

审核引擎和完整 regression 资产的迁移合同由 #3 拥有；具体 legacy baseline 不在本文件重复。

迁移 package 时以**最快达到 #3 parity 并降低终态上下文**为准：

- 现有 package 已经内聚、可工作且 PPAP 当前需要时，可以整体迁入；
- pytest、fixture、oracle、配置和实现随能力一起迁入并由本仓维护；
- legacy baseline 本来存在的算法 failure 不要求在迁移中修复；
- 迁入后删除无消费者代码、跨仓 import、源码路径和 CI checkout；
- 不为未来可能的格式/provider/Agent 平台预建额外抽象。

KISS 的目标是最终系统更少上下文，不是要求迁移过程必须最小粒度。
