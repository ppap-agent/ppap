# 规则程序

编译只读取完整的冻结生效规则，不读取供应商文件。输出以调用方 schema 为准。规则原文拥有业务义务，程序是可重放的解释，来源定位校验不能证明解释正确。

## 编译步骤

1. 逐句核对完整原文的对象、字段、前提、适用条件、要求、否则分支、方向、量词和指代，在 working-state.json 简记它们对应的程序位置。不能跳过中间条件、据标题套模板或静默统一不同指代。已支持的值运算直接写成 value_test。
2. 将完整 CompiledRulePlan 写入 execution.json。按下方公开方法校验 schema，再核对每条 ID、来源和修订与冻结输入一致。artifact_schema.json 提供完整字段合同；无需读取 trace.py、bindings.py 或 validation.py。
3. 用下方小型 synthetic 例子检查关键算子逻辑。保留已知满足、已知违反、未知、AND 和适用性反例；按本条原文替换相关表达式、值及预期。有明确空范围政策时补查该政策。自检使用正式的 evaluate_audit，不另写判定逻辑，也不要求为自检建立完整供应商对象模型或绑定全部规则。把本条表达式、构造值、预期和实际状态记在 working-state.json。
4. 重读完整文件，确认 schema、来源和分支检查通过，再提交文件和完成回执。合成例子只检查算子与编译选择，不能证明整条业务解释正确，不能作为供应商证据、人工确认或训练 gold。

## 公开校验方法

在当前编译工作目录运行以下 Python。schema 校验针对实际 execution.json。算子示例使用独立的两字段单行快照和仅供自检的 RulePlan，不改变实际计划、ontology、冻结来源或供应商文件。示例的 NA 是明确声明的合成零适用对象政策；实际计划仍使用原文政策。按需修改本条关键表达式及构造值，无需读取实现代码。

```python
from pathlib import Path
from ppap_deep.semantic.models import CompiledRulePlan, RulePlan
from ppap_deep.semantic.trace import AuditTrace, evaluate_audit

CompiledRulePlan.model_validate_json(Path("execution.json").read_text())
A = {"kind": "value_test", "field": "A", "op": "not_empty"}
B = {"kind": "value_test", "field": "B", "op": "not_empty"}
AND = {"kind": "all", "items": [A, B]}
UNKNOWN = {"value": None, "state": "unknown_formula_cache"}

def status(require, a, b, when=None):
    cell = lambda value: value if isinstance(value, dict) else {"value": value, "state": "known"}
    document = {
        "source": {"file": "synthetic.xlsx", "format": ".xlsx", "sha256": "synthetic"},
        "tables": [{"id": "Records", "rows": 2, "cols": 2, "merges": [], "cells": {
            "A1": cell("A"), "B1": cell("B"), "A2": cell(a), "B2": cell(b),
        }}],
    }
    program = {"kind": "records", "source": "source", "quantifier": "forall", "require": require, "when": when}
    plan = RulePlan.model_validate({"source_sha256": "synthetic", "plan_revision": "0" * 64, "rules": [{
        "id": "R1", "source_locator": {"kind": "published_clause", "clause_id": "R1"}, "program": program,
        "empty_scope_status": "NA", "empty_scope_basis": "Synthetic unit case: known false applicability means NA.",
    }]})
    trace = AuditTrace.model_validate({"bindings": [{
        "id": "data", "document_id": "source", "source_sha256": "synthetic", "table": "Records", "header_rows": [1],
        "first_row": 2, "last_row": 2, "columns": {"A": "A", "B": "B"},
        "mapping_basis": "Synthetic headers A/B", "boundary_basis": "Complete synthetic single row",
    }], "uses": [{"audit_id": "source", "rule_id": "R1", "inputs": {
        "source": {"kind": "bound", "scopes": [{"binding_id": "data"}]},
    }}]})
    return evaluate_audit(trace, {"source": document}, {"source": plan}).report.checks[0].status

assert status(AND, "x", "y") == "PASS"
assert status(AND, "", "y") == "NG"
assert status(AND, "x", "") == "NG"
assert status(AND, "x", UNKNOWN) == "UNABLE"
assert status(AND, "", UNKNOWN) == "NG"  # Independent known violation remains.
assert status(B, "", "", when=A) == "NA"  # Genuine applicability differs from AND.
assert status(B, "x", "", when=A) == "NG"
assert status(B, UNKNOWN, "y", when=A) == "UNABLE"
```

## 来源和缓存

按冻结顺序保留每条 ID、source SHA 和 plan revision。已发布 JSON 使用本条 `published_clause`。原文件规则从本条冻结 `source_locators` 选择正文位置，并核对完整上下文。已发布正文优先于原始附件；标题、示例和历史规则不增加本条义务。

RulePlan 的 ontology 声明本次必要 entities：id 与逻辑 input 同名，grain 说明业务对象粒度，attributes 给出 string/number/boolean 类型，identity 只引用已声明属性。relationships 声明源、目标及有方向的业务含义。类型别名不证明实例相同；不得默认固定 PPAP 对象词表。审核程序仍使用同一个 evaluator。

appendix 由宿主从冻结有效规则复制。编译时完整阅读它，但不能自己生成确认内容；文件特定说明不能推广到其他文件或写进通用映射。实际编译、缓存读取及最终 bundle 均使用强制 ontology 的 schema：entities 至少一个，必须明确粒度、属性和身份。旧修订或缺概念的缓存重新编译，不用空概念默认值进入当前产品。纯求值单元仍可手构旧行式 RulePlan，以验证原算子；它不构成当前编译入口。

程序只包含逻辑字段、条件、比较和问题。供应商文件名、坐标、观察、答案及审核结论属于本次执行，不能写入可复用程序。字段名保留业务含义和必要的父表头路径。

后续文件证据可以澄清本次对象粒度或加强已声明的身份分量；此类解释由 AuditTrace.plan_refinements 限定到本次目标，并由同一 evaluator 校验和执行。基础 RulePlan 与通用规则缓存不变。该修订不能改变条款、关系含义、量词、适用条件、比较或规范化政策，也不能移除身份分量；规则含义仍有合理歧义时，使用现有解释 Hole 和已确认附录流程。来源可读不证明语义等价，编译和修订都要以完整规则为依据。

## 编译判定逻辑

不要先把整条规则分成机械或语义两类。一个程序可以组合确定性计算和局部语义判断。

- `document`：单个文件级义务，例如审批或勾选状态。`require` 可用 Judge 指向冻结文档中的事实。
- `records`：每个受审对象的义务。`source` 是逻辑源集合，`quantifier` 为 forall 或 exists。宿主完整展开源集合，不从匹配结果反推源集合。
- `precondition`：必须成立的文件或表结构条件。只接受 collection_exists、column_exists、文件级 Judge 及其布尔组合。已知 false 是 NG；true 不能替代正文覆盖。
- `when`：原文明示的当前行适用条件。只接受 value_test、局部 Judge 及其布尔组合。false 才排除对象；unknown 保留未知。不能把一个失败条件改成不适用，也不能用列存在性筛行。
- `records.require`：当前行必须满足的值条件、Related 及其布尔组合。不能使用列或集合存在性。`all/any/not` 执行三值逻辑；已知独立违反可支持 NG。

“A 且 B 则通过，否则不通过”编成 require=all(A,B)。只有原文明示“仅当 A 时要求 B”，才编成 when=A、require=B。检查真、假、未知和空范围，核对 AND、OR、否定、方向及量词。不得从样本标签倒推程序。

前后指代不一致，且多种合理解释会改变检查对象、字段或结果时，用局部 Judge 保留整个未定子要求。question 写清原文涉及的对象、字段、分支和未定含义，fields 包含各候选所需的真实逻辑属性；将合理读法直接写入 Judge.candidates，每项为 id、label、explanation，最多八项。候选随计划进入审核，宿主派生现有 interpretation Hole 并在确认前使整个 Judge 保持未知，不改原生字段值；独立明确义务仍确定性求值。普通事实 Judge 的 candidates 留空。不要先按某个未确认候选写值条件，再将未知 Judge 与之 AND，称该候选的失败是独立违规。逻辑可执行本身不证明指代唯一。名称不同或原文明示跨对象关系本身不构成歧义；含义唯一时照原文执行。仍可成立的完整字面组合须保留为可行读法；候选若改变原文明示的对象、条件或要求，须说明属于标准修改，不能伪装为等价别名，确认后按新生效规则重新编译。

例如两项内容均须非空，行级 require 为 `{"kind":"all","items":[{"kind":"value_test","field":"A","op":"not_empty"},{"kind":"value_test","field":"B","op":"not_empty"}]}`。A 已知为空、B 已知非空仍违反要求；将 A 放进 when 会漏掉该反例。

## 有限运算

`value_test(field,op)` 对当前行的原生字段执行 empty/not_empty/eq/ne/in/gt/ge/lt/le。`column_exists(input,field)` 只检查列是否存在。`collection_exists(input)` 区分已找到、已搜索而确认不存在、尚未定位。schema 将这三种表达的可用位置分开；文件级 require 与 precondition 使用相同的结构表达。

例如“须有频次列，且该列所有内容非空”：precondition 为 column_exists，records.require 为 value_test(op=not_empty)，source 为该表的逻辑记录集合。即使列存在，一条空单元格仍应 NG。表头中多个名称可能是同一业务字段的别名时，使用一个清楚的逻辑字段，并在执行时根据完整表头定位；不要给每个别名制造独立的值义务。

`related` 从当前 source 行指向完整 target 集合。明确 source_key、target_key、match 和 comparisons。any 要求至少一个对应记录满足；all 要求有对应记录且全部满足；one_to_one 还要求源键唯一及目标恰好一条。单向关系不自动产生反向义务。

每个 related 引用 ontology 中确切的 relationship_id。关系 kind 为 same_instance 或 association，与 key_match 的字面／语义／有依据端点匹配是不同维度。same_instance 的两侧 key.fields 分别严格等于各自实体按语义对应顺序声明的 identity，使用分量元组，不允许 separator 或 template，避免不同身份的字面拼接碰撞。association 可以按外键建立一般关联，也可以表达规则明确要求的字面拼接查询；其键不要求等于实体 identity。

键默认为保留位置和类型的元组。只有原文支持时才使用显式 separator 或 template；separator 是值之间的字面分隔符，如空格，不能包含字段占位符。格式模板如 `【{type}】【{kind}】{desc}：{value}`。两者互斥。产品和过程两种对应要求分别声明 Related，再按原文用 all 或 any 组合。`target_when` 只接受目标行的值条件或局部 Judge；未知目标资格仍是可能候选，不可删除。key_match=semantic 只在原文需要语义身份时使用；固定标识符优先精确匹配。

原生数值 10 和 10.0 相等；布尔、文本 "10"、编号 "010" 仍区分。默认不删除标点、不纠正 O/0、不做子串相等、不猜单位换算。whitespace 和 casefold_whitespace 必须由原文授权。绑定不能临时改变这些政策，也不能把计算后的字符串冒充原始列。

`judge` 只用于当前有限运算不能确定的局部问题。写清问题及必需的当前行 fields、逻辑 inputs。宿主提供当前行、相邻行、集合身份和有序范围引用；需要日期首尾、下一行、图像或含义判断时，读取相应原件片段后回答。文件级问题没有当前行，fields 仅作为原文搜索提示，不表示已读取的字段值；可查看冻结文件的段落、页和区域。有来源的明确端点关系事实可使用 key_match=evidence；这不改变原条款身份依据或比较要求。语义值比较用 comparison.mode=semantic，只对实际不同的已知值提出问题。原始行值未知时不能用布尔答案伪造已知值。ontology 已声明标量类型时，宿主可提出有类型的 attribute Hole；模型提供有来源的解释，宿主比较该值，原生未知观察仍单独保留。

## 缺失、未知与完整范围

已知空单元格执行字段谓词；不可读、公式无缓存、字段映射或行角色不清楚都保留未知。原文明示缺列或缺文件 NG 时，将 column_exists 或 collection_exists 编入 precondition。未定位不是确认不存在；缺列、缺表和缺文件是不同事实。

每个失败前提都要能指向正文或已确认附录中的要求。依赖基准文件进行比较，本身不足以规定“缺少基准文件判 NG”；原文未规定缺件政策时，保留 UNABLE，并将可行政策及影响交给规则解释澄清。编译自检同时检查正常输入和缺少基准的输入，避免只在齐全样本上得到相同标签，却悄悄增加义务。

记录适用性与物理绑定分开。组标题可参与工序覆盖，但不是特性明细；每个 input use 自行声明成员。所有相关表和文件都要进入完整集合；重复键保留，不能先去重或只选最佳单表。

`forall` 可由独立已知反例支持 NG。`exists` 有已知命中但仍有未完成范围或观察时，公开结果仍是 UNABLE。未知范围不能执行空范围政策。

每条规则必填 empty_scope_status 和 empty_scope_basis。只有有效原文明示零适用对象可 PASS 或 NA，才使用该政策；否则 UNABLE。已确认缺对象的失败前提优先。完整候选范围全部确定为不触发，才是已证明零适用对象。

完成前核对每个条款的来源、对象、前提、适用条件、必要条件、方向、量词、类型、缺失及空范围。有限表达之外使用明确的局部 Judge；不要新增任意 Python/SQL 程序或改写供应商事实。

原生观察保留 reader_parameters。只有同一原件、格式、解析参数的缓存才可复用；不同参数重新读取，不能借旧缓存绕过较小的读取上限。逻辑对象、属性选择及局部答案不按文件单独缓存：同一文件的另一 ontology/附录/程序仍需重新构建和验证。

occurrence 是一份来源表示，有自己的 ID、值和位置；ontology identity 定义条款下的业务对象。代表同一业务对象的多份表示使用 same_instance，保留每份表示、冲突和重复计数。只有行或文件不同不能证明 association；独立业务对象或原文明示的字面关联才使用它，含义未定时保留 Hole。
