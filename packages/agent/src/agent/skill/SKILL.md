---
name: ppap-semantic-audit
description: 从冻结 PPAP 规则编译可缓存的程序，绑定原始文档，由同一求值器执行全量计算，并回答局部语义问题。
---

# PPAP 语义审核

生效规则决定审核要求。程序、绑定和模型答案都可存在理解错误；结构有效不等于业务正确。

## 按阶段读取

- 编译阶段只读冻结规则和 [规则程序](references/rule-plan.md)。调用方已在提示中注入该指南时直接使用，无需再次读取。供应商观察、绑定和答案属于执行阶段。
- 执行阶段按 [执行证书](references/execution-trace.md) 完成绑定、求值和局部答案。调用方已注入该指南时直接使用。表格布局问题再读 [通用表格读取](references/table-reading.md)；PDF 视觉问题再读 [PDF 建模](references/pdf-reading.md)；复杂跨文件关联再读 [跨文件比较](references/cross-file.md)。
- 查看原件或求值命令的参数时，读 [工具](references/commands.md)。公开 schema 由调用方写入 artifact_schema.json；无需阅读实现文件来构造结果。

最终将本阶段 schema 对应的完整 JSON 写入 execution.json，再按调用方合同返回完成回执。正式报告由宿主的同一 evaluator 生成。未知保持未知，独立已知违反仍可 NG。一次协议修复使用原冻结来源及程序。

## 复用

程序按租户、规则来源及引擎修订缓存。绑定和局部答案属于本次文档；规则或文件版本、映射或用途变化后，问题身份变化，旧答案不能复用。已有 Excel checker 的字段知识、纯计算及反例可参考；其标题匹配、删标点、子串相等和单表猜测不是默认审核政策。
