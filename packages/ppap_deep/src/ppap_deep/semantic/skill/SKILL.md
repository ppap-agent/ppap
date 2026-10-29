---
name: ppap-semantic-audit
description: 从冻结 PPAP 规则编译可缓存的程序，绑定原始文档，由同一求值器执行全量计算，并回答局部语义问题。
---

# PPAP 语义审核

生效规则决定审核要求。程序、绑定和模型答案都可存在理解错误；结构有效不等于业务正确。

## 编译阶段

只读取本次冻结规则及 [规则程序](references/rule-plan.md)。按其步骤生成 RulePlan 文件，调用同一 schema 和 evaluator 检查构造反例，再按调用方合同提交。程序保留原文的对象、条件、方向、量词、比较及空范围政策；供应商坐标、观察或答案属于执行阶段。

## 执行阶段

1. 阅读冻结条款、plans.json、targets.json、document-index.json。上下文压缩后重读 context-index.json 和 obligations.json。
2. 按 [通用表格读取](references/table-reading.md) 探测 layout、表头、边界与例外，按需读原始片段。机器读取完整绑定范围；模型不必抄录所有数据。PDF 视觉问题见 [PDF 建模](references/pdf-reading.md)。
3. 按 [执行证书](references/execution-trace.md) 从宿主提供的 initial-execution.json 开始，按 obligations.json 的字段视图补全 bindings 和 uses，逐步保存候选。物理绑定保留全行，集合成员资格随用途声明。跨文件规则见 [跨文件比较](references/cross-file.md)。
4. 调用同一 audit_tools evaluator 查看全量计算及局部问题。回答真实 need_id 后重放。正式报告由宿主生成，模型不输出逐行 verdict 或聚合数量。命令见 [工具](references/commands.md)。
5. 未回答、未定位及真实不可读均保留未知；其是否影响结论由程序的三值逻辑决定，独立已知违反仍可 NG。可选引用缺口不改变业务状态，也不单独触发模型修复。模型不得改写缓存程序来配合观测结果。

最终只按 schema 返回 AuditTrace。模型调用成功、合法 JSON、相同数量和有证据字段都不能独立证明审核正确。一次协议修复使用原冻结来源及程序；不新增模型客户端或自行递归重试。

## 复用

程序按租户、规则来源及引擎修订缓存。绑定和局部答案属于本次文档；规则或文件版本、映射或用途变化后，问题身份变化，旧答案不能复用。已有 Excel checker 的字段知识、纯计算及反例可参考，但标题匹配、删标点、子串相等和单表猜测不是默认审核政策。
