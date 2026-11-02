# 检查

按本次生效条款和目标文件编写检查。不要手写审核结果。

检查写入 `checker.py`，并提供 `create_audit(audit_file, rule, group_context=None)`。它返回带 `checks` 和 `close()` 的审核对象。需要本次文件自己的资源时，再写 `resources.py`。

## 判断

- 一个条款一个入口、一项结果。条款 ID 来自本次生效规则。
- 读不到、不适用、技术失败分别是 `UNABLE`、`NA`、`ERR`。
- 空范围、零断言或未执行不能是 `PASS`。规则写明空范围时，调用 `ctx.record_empty_scope(状态, 依据)`。状态只能是 `PASS`、`NA` 或 `UNABLE`，依据不能为空。这不能掩盖已收集的 NG。
- 方法末尾的 `PASS` 不能掩盖已经收集到的 NG。
- 规则要求全部满足时，先调用 `ctx.require_all()`。范围被提前打断，或仍有未解问题时，不能保持 `PASS`。
- 规则只要求存在一个时，先调用 `ctx.require_one()`，并用 `ctx.mark_witness()` 记录有效见证。
- 搜索未完成时调用 `ctx.note_search(found=False, exhausted=False, label="...")`。不能把未完成搜索写成确认不存在。
- 明确反例可以是 `NG`。其他未解问题用 `ctx.note_unresolved()` 保留。
- 含义需要用户决定时，调用 `ctx.ask(问题编号, 问题, ((候选编号, 名称, 解释), ...))`。至少两个候选。这不能因此返回 `PASS`。
- `row.observation(列名)` 只有 `state` 和 `value`。不要读取 `unreadable`、`known` 或 `missing` 属性。`state` 只能是 `known`、`blank`、`missing` 或 `unresolved`。`0` 和 `False` 仍是 `known`。`blank` 不是确认不存在。`unresolved` 不能当成已知值，也不能因此返回 `PASS`。
- 关联文件的条款在 `group_context.rules_by_id`，键是关联文件的版本 ID。不要用目标文件的条款代替它。预览不是完整扫描，也不能把关联单元格记到目标文件。
- 打开关联工作簿后，先 `ctx.bind_workbook(workbook, 关联文件版本ID)`，再读取。只记录一个关联单元格时，再调用 `ctx.note_cell(表名, 行号, 列号, file_version_id=关联文件版本ID)`。版本 ID 来自该文件的 `file_metadata["file_version_id"]`。摘录只用单元格原值。

## 写法

复用 `ExcelAgent`、`@rule(..., clause_id="条款ID")` 和原 Fluent API。类属性设置 `CLAUSE_BOUND_CHECKS = True`。

直接导入：

- `from audit.checkers.v3.excel_agent import ExcelAgent, rule`
- `from audit.engine.rule_engine.context import RuleContext`
- `from audit.engine.rule_engine.fluent import Table`
- `from audit.engine.rule_engine.contracts import RuleResource`
- `from documents.pdf import PdfSpec`

PPAP 预设也从 `documents.pdf` 导入，例如 `PPAP_8_PROCESS_FLOW_CHART_SPEC`。PDF 审核把预设赋给类属性 `PDF_SPEC`。

`resources.py` 定义 `RESOURCE`，并由 `ExcelAgent(..., resource=RESOURCE)` 传入。任务资源与该文档类型的注册表资源进入同一个 `ctx.resource`。同名项以任务资源为准。试跑出现「未定义数据区规范」时，按目标文件的实际表名和列名写资源。尖括号只是占位，不能原样执行：

```python
from types import MappingProxyType

from audit.engine.rule_engine.contracts import ColumnModel, DomainTokensModel, RuleResource, SectionModel, SheetModel

SHEET = SheetModel(canonical_name="<本次工作表>", include_keywords=("<本次工作表>",))
SECTION = SectionModel(
    canonical_name="<本次数据区>",
    sheet_name="<本次工作表>",
    header_anchors=("<列甲>", "<列乙>"),
    row_anchors=("<列甲>",),
)
COLUMN_A = ColumnModel(canonical_name="<列甲>", section_name="<本次数据区>", aliases=("<列甲>",))
COLUMN_B = ColumnModel(canonical_name="<列乙>", section_name="<本次数据区>", aliases=("<列乙>",))
RESOURCE = RuleResource(
    sheets=MappingProxyType({SHEET.canonical_name: SHEET}),
    sections=MappingProxyType({SECTION.canonical_name: SECTION}),
    columns=MappingProxyType({COLUMN_A.canonical_name: COLUMN_A, COLUMN_B.canonical_name: COLUMN_B}),
    field_blocks=MappingProxyType({}),
    fields=MappingProxyType({}),
    tokens=DomainTokensModel(),
)
```

## 试跑回执

宿主执行 `check()`。回执不包含审核结果。

- `{"completed":true,"artifact":"checker.py"}`：运行全部生效条款。
- `{"completed":true,"artifact":"checker.py","clauses":["条款ID"]}`：只运行这些条款。局部试跑不能固定程序。
- 修改 `resources.py`，或修改条款方法以外的共享函数后，下一次运行全部生效条款。
- 覆盖全部条款后，若判断符合本次条款，再次提交同一程序且不要带 `clauses`。这表示固定。
- 不要把已完成条款的 NG、UNABLE 或 NA 改成 PASS。可执行只说明程序跑完，不说明业务判断正确。
