# 可重放的执行产物

调用方使用 AuditExecution schema 时，返回 `report` 和 `trace`。每个 `(audit_id, rule_id)` 恰好有一个 proof。

## 表格条件

可用的原子条件是 `not_empty / empty / eq / ne / in / gt / ge / lt / le`。同一 proof 的 predicates 按 AND 组合；quantifier 为 forall 或 exists。
这就是当前 query DSL 的完整组合能力：没有 OR、条件 WHERE、蕴含、聚合、排序、缺列判定或跨表/跨文件 JOIN。`scope` prose 不参与可信重放。完整规则中有任何不可表达分支时，计划必须选择 semantic program。

先建立 binding：source_sha256、document_id、table、header_rows、first_row、last_row、columns、excluded_rows、mapping_basis、boundary_basis。
行列来自本次文件观察。正文区是闭区间；每个排除行必须包含 row、reason_type、evidence_locator 和 reason。同一个 binding 可供多个规则共用。
表头必须在表内且不与正文区重叠，可以在正文区之前或之后。

再返回 query proof，例如：

```json
{"kind":"query","audit_id":"a","rule_id":"1","binding_id":"control-plan"}
```

query proof 只能引用 RulePlan 中同一 rule_id 的结构化 predicate，不能再次定义条件。可信代码在已冻结快照上重新执行该 predicate。`total` 是未排除的正文记录数；`unknown` 是无法确认条件的记录数；`checked = total - unknown`。
QueryProof 只能对应 `predicate.kind=query`；SemanticProof 只能对应 `predicate.kind=semantic`。审核执行和修复都按计划生成相同 kind，不能在运行时切换。`trace.bindings` 只列出被 QueryProof 的 `binding_id` 实际引用的 binding；semantic proof 不需要 binding，未引用 binding 会使完整校验失败。
forall 的确证反例为 NG；没有反例但有未知为 UNABLE；所有记录成立且非空才 PASS。
exists 的确定命中可支持条件，但公开 PASS 仍要求 unknown=0。空范围为 UNABLE。
NG 的公开 evidence 必须指向重新执行后确认的反例行。

空白只包括 null 或空白字符串，0 和 false 都不是空白。eq/ne/in 保留标量类型，不做文本/数值转换；数值大小比较只接受数值。
合并区域沿真实锚点取值；未知公式缓存保留未知。运行时有累计访问预算。

## 语义或当前表达式不支持的条件

保存 semantic proof：`scope_basis` 说明候选、适用、排除和未知范围，`records` 每项包含唯一 record_id、role、status、reason、typed evidence。`role=prerequisite` 是文件、列、边界等前置观察；`role=applicable` 是实际受审对象，需 `applicability_basis` 和来源证据；`role=excluded` 是未纳入适用集合的候选对象，需 `exclusion_basis` 和来源证据，已确认不触发时 status 为 NA，仍不确定时为 UNABLE。单个页眉或文件存在性要求本身也可成为适用对象；不能仅把前置 PASS 重标为 applicable 来绕过空范围检查。
跨文件关联、复杂 JOIN、图像语义等不能用上述原子条件忠实表达时，使用逐记录语义判断；已确认的 SQL/Python 结果可辅助判断，并在 reason 中明确实际计算与语义解释。

状态与计数从记录列表聚合：有 NG 则 NG；否则有 UNABLE 则 UNABLE。没有 applicable 记录时，按计划的 `empty_scope_status` 判定；PASS/NA 只在有效正文明确授权、计划有依据时成立，否则 UNABLE。已排除候选的 NA 状态描述该候选不触发，不覆盖规则对空集合的明确结论；公开证据应支持这一空范围。有效规则本身未说明该情况时，UNABLE 用 `diagnostic=rule_ambiguity`，不要误写为 parser_unknown。存在 applicable 记录且无 NG/UNABLE 时为 PASS。计划与 proof 结构只使判断可审查，不自动证明自然语言解释正确。
`total` 是观察记录数，不等于业务正文行数；`checked` 是非 UNABLE 观察数，`unknown` 是 UNABLE 观察数。
每条非 UNABLE 记录必须有可解析的已知证据。公开结论的 evidence 必须来自支持该状态的记录。
未完成的观察保留为 UNABLE，不能从记录列表中删除后宣称覆盖完整范围。

## 单次执行修复

校验修复输入包含已有 snapshots、当前 plans、先前 schema-valid AuditExecution 和具体校验反馈。复用这些产物并返回一份完整替换 execution；只修正反馈涉及的问题，仍须满足全部 trace、证据、覆盖和计数规则。proof kind 继续服从计划。调用方只执行一次 repair，失败后不会把新输出再次递归送修。

完成条件：报告计数和状态与 proof 一致；每个规则有来源、范围、执行记录；无法确认完整范围时返回 UNABLE。
类型和计数校验不证明模型选择的字段、正文边界或自然语言解释一定正确，这些仍属于业务准确率验收。
