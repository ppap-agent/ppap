# 规则计划：JSON + SQL

这是 agent 生成和检查的中间表示，不是自动理解自然语言的编译器。SQL 仅负责已建立语义字段的数据查询，不承担文档布局推断。先保存计划，再执行；发现规则解释变化时显式更新计划及理由。

## 内部语义表示与输出契约

调用方提供的 output_schema.json 是输出契约；下例用于解释语义，不是另一个输出 schema。
紧凑 schema 中用 scope、predicate、missing_evidence 保留范围依据、量词、字段要求、比较方式和异常分支；
source_locator 使用结构化位置指向生效规则；引文由可信 resolver 从该位置读取，source_sha256 和 plan_revision 位于计划顶层。已发布 JSON 规则使用 `{"kind":"published_clause","clause_id":"R.1"}`；这是稳定的有效正文条款 ID，不是原始附件坐标。旧文件规则继续使用真实 DOCX、Excel 或 PDF 位置。

当前 `QueryProgram` 只表示：同一条记录上的一个或多个标量 predicate 按 AND 组合，再用 `forall` 或 `exists` 覆盖完整 binding。它没有条件 WHERE、OR、蕴含、聚合、排序、缺列分支、跨表或跨文件 JOIN。`scope` 是供人和 semantic proof 说明范围的文本，不是可执行过滤器。只有整条规则的所有分支都能由当前 query schema 忠实重放时才用 `kind: query`；任一分支不能表达时，整条规则用 `kind: semantic`。

## 每条规则的语义示例

```json
{
  "id": "R.1",
  "description": "正文每条记录的频次均非空",
  "priority": "P1",
  "source_locator": {"kind": "docx_cell", "table": 1, "row": 7, "column": 1},
  "scope": "绑定表中的全部正文记录",
  "predicate": {"kind": "query", "quantifier": "forall", "predicates": [{"field": "frequency", "op": "not_empty"}]},
  "missing_evidence": "已绑定字段的单元格解析未知时为 UNABLE",
  "empty_scope_status": "UNABLE",
  "empty_scope_basis": "有效正文未规定零条正文记录时可通过或不适用",
  "ambiguities": []
}
```

输出结构以调用方 schema 为准。规则 ID 保留原文编号；上例名称仅示意。计划不保存当前待审文件的 binding，审核另行建立并由 QueryProof 引用。

## 编译原则

| 原文要求 | 表示方法 | 容易丢失的含义 |
|---|---|---|
| 全部、每一项 | `forall` + 违规查询 | 前置条件、空集合、未知值 |
| 至少一个、存在 | `exists` + 命中查询 | 不能抽样未命中就判不存在 |
| 当 A 时 B | 整条规则使用 `semantic` | 当前 query 没有可执行 WHERE/蕴含；A 未知不等于 A 为假 |
| 缺少必需列即 NG | 整条规则使用 `semantic` | query binding 要求字段已绑定，不能表达缺列分支 |
| A 均在另一表或文件的 B 中 | 整条规则使用 `semantic` | 当前 query 没有 JOIN、连接键或集合差 |
| 第一个／最后一个 | 整条规则使用 `semantic` | 当前 query 没有排序键 |
| 分值为最大／最小 | 整条规则使用 `semantic` | 当前 query 没有分组或聚合 |
| 内容含三个维度 | 整条规则使用 `semantic` | 三个关键词出现不等于三个要求都满足 |

`missing_evidence` 记录规则对缺少表、列、文件组、标记解释或单位的分支。缺少必需字段与已存在字段为空应分别记录。原文未定义某分支且影响结论时保留歧义，不自行补一条政策。

当前 query 重放对空 binding 范围固定返回 UNABLE。规则若要求空适用范围为 NA/PASS，整条规则使用 semantic program。正文为空、类别无法解释或列根本不存在不能借零行结果自动通过。
每条计划还必须记录 `empty_scope_status` 和 `empty_scope_basis`：只有有效正文明确授权时才用 PASS 或 NA，并说明该授权如何适用；未授权时用 UNABLE。明确规定的缺字段、缺文件 NG 分支仍先执行，不被空范围策略覆盖。旧计划缺少这两个字段时不能直接复用。

## 字段映射与判定分开

字段映射记录：`概念 → 表头路径 → 数据列 → 证据 → 已确认/候选/未知`。同一个概念在所有规则共用该记录。例如从多层表头的“现行过程控制 → 探测”识别探测控制字段，不必要求它写成一格；但原文若明确要求出现“现行探测控制”有效字符，还需单独执行字面标签条件。

允许字段语义映射不意味着允许改变数值、阈值、必填项或相等定义。拼接后的精确相等、忽略空格、同义相等必须明确区分。规则标题和正文冲突时记录两者，不以标题静默改写正文。

结构歧义与业务歧义分别列出。例如“列名不完全相同但角色明确”通常属于映射；“▽到底属于 CC 还是 SC”需要符号图例或客户定义。可以先检查所有已标记行的控制方法是否非空；若全部满足，可说明该结论无需依赖尚未确认的分类。但不能据此确认符号含义。

## 语义条件的证据

无法由 QueryProgram 完整重放的条件使用 `SemanticProgram.question`，例如：

```json
{"field":"effect","question":"每一条失效影响是否分别说明内部、客户工厂、最终用户的影响及各自分值？","coverage":"all relevant records","output":"逐行 true/false/unknown，附来源和简短理由"}
```

复用同一字段映射及已提取事实。将明确的 false 作为反例；有 unknown 时，不能仅依据其他 true 判全量通过。

## 查询语义核查

- 区分 `A AND B` 与 `IF A THEN B`。若要求每条记录的两个字段都非空，不能把第一个字段非空放进 WHERE，从而过滤掉本来应报错的记录。编译完成后用“第一个字段为空、第二个字段有值”的反例核查表达式。
- 集合差异可用有明确连接条件的 `NOT EXISTS` 辅助 semantic proof，并另查空连接键；它不能成为当前 QueryProgram。`NOT IN` 的右侧只要含 NULL，就可能隐藏所有未匹配项；零结果不能绕过这一问题。
- 保留数据类型。数值 1 与 1.0 的渲染差异不应自动成为业务差异；也不能把所有编号 CAST 为数值，因为 `01`、字母编号或非法字符串可能被改变。先确定字段类型和允许的规范化，再比较或拼接。
- 审核辅助查询使用的每个表/视图都需有实际绑定或派生定义。尚未物化的视图、草拟 SQL 和错误执行结果不能当作已完成的检查；semantic proof 仍需保存逐记录 observation 和 typed evidence。

## 受限条款复核

当可信校验发现 planned predicate kind 与 execution proof kind 不一致时，只复核受影响条款。复核输入是原始规则来源、冻结的有序条款 ID 和现有计划；不读取待审文档、审核报告或其他待审内容。

按调用方 schema 返回每个 reviewed clause 及其 reason。条款 ID 必须保持不变，source locator 仍须定位到原始规则来源并通过同一可信校验。复核可以确认原计划无需修改；不要为了配合错误 proof 改写规则，也不要改动未受影响条款。

## 完成检查

编译后按原文顺序复核一次：规则数、前置条件、AND/OR、全称/存在、比较方向、排序、单位、缺失分支、示例与约束的区别。记录解释歧义和解释对结论的影响。计划中不需要嵌入整份规则文档，但必须能找回每个条款的源位置与摘要。
