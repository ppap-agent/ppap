# 检查

按本次生效条款和目标文件开发任务专用检查。正式结果来自程序执行。

1. 有 `current-checker.py` / `current-resources.py` 时，先复制为本轮的 `checker.py` / `resources.py`，避免中断丢失起点。然后读取 `reference-checker.py` 的相关原方法和 `reference-resources.json`（若存在）。用 `template-checker.py` 的任务类继承原 checker；优先调用语义相符的父类方法和辅助函数。原编号与本次条款身份分开。
2. 若有 `current-checker.py` / `current-resources.py`，它们是最近候选，包含失败或局部试跑版本。结合本次诊断继续修改这些源码。
3. 写入 `checker.py` 和必要的 `resources.py`。按每个实际条款绑定一个入口；模板只把本次方法装配为 `checks`，保留父类辅助能力。
4. 尽早写出可试跑候选，再按缺口检查原件。提交试跑回执。阅读执行反馈，修复资源、扫描或判断错误。全部条款跑完并核对规则后，再提交同一程序以固定。

原源码是经验依据；本次条款和文件决定适用性。源数据只读。教学与开发只使用提供的规则、文件和实现；测试预期、其他实验评分和历史最终判定不参与生成。

检查写入 `checker.py`，并提供 `create_audit(audit_file, rule, group_context=None)`。它返回带 `checks` 的审核对象；持有需要释放的资源时实现 `close()`。需要本次文件自己的资源时，再写 `resources.py`。

完整 `RuleInput` 保留客户、车型、零件、预置来源、条款和已确认附录。V4 的 `reference-bindings.json` 是原条款 ID → `[原文, 方法名]`；先比较本次正文和附录再复用方法，相同 ID 不能证明语义仍相同。`group_context` 包含本组全部文件，其他 `target` 也可作为关联资料；仅为当前目标执行本次条款，并按文件版本记录来源。规则中的客户适用范围不是文件中已经读到的客户身份。

## 判断

以下 `ctx` 与 `row` 方法属于 Excel / Fluent 路径。UAES 原生方法使用下一节说明的事实读取和异常合同。

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

## Excel / Fluent

复用 `ExcelAgent`、`@rule(..., clause_id="条款ID")` 和原 Fluent API。类属性设置 `CLAUSE_BOUND_CHECKS = True`。

直接导入：

- `from audit.checkers.v3.excel_agent import ExcelAgent, rule`
- `from audit.engine.rule_engine.context import RuleContext`
- `from audit.engine.rule_engine.fluent import Table`
- `from audit.engine.rule_engine.contracts import RuleResource`
- `from documents.pdf import PdfSpec`

同一文档类型的 PDF 可以有不同版式。不要按文档类型或文件名选择预设。预设是其他文件的例子，不是本文件的版式。

此路径的目标 PDF 必须把 spec 赋给类属性 `PDF_SPEC`。关联 PDF 写入类属性 `PDF_SPECS`，键是 `file_version_id`，并用 `self.load_related_pdf(file_version_id)` 打开，再调用 `ctx.bind_workbook` 记录来源。开发期间可以修订 spec；`template-pdf-spec.py` 只提供候选。配置变化后重新转换并完整试跑。没有可用文字网格，或转换后定位不完整时，保持 `UNABLE`，不要改成 `PASS`。

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

## UAES 原生方法

模板直接继承 Resolver 选出的 UAES 类。保留模板中的 `bind_native_checks(self)`；它使用原 `RuleItem` / `RuleCheck`，按显式 `clause_id` 调用原 `check(ith)`，并保留每项独立的证据与完成计数。数量与顺序由本次条款决定，继承的方法只提供实现经验。任务方法无 `ctx` 参数，例如：

```python
@rule("本次条款", clause_id="本次条款ID")
def check_001(self) -> tuple[str, str]:
    return super().rule_1()  # 已核对它与本次条款及附录一致时复用
```

布局适配沿用参考实现的别名、`native().field(...)`、`native().rows(...)`、`related(...)` 和 `witness(...)`；可将本次别名写在 `resources.py` 后显式导入。确认缺失的业务事实用 `MissingFact`，未知事实用 `UnknownFact`；读取或程序故障保留 `ERR`。成功读取仍须按规则完成比较和范围扫描；仅有来源不能证明规则实现正确。无业务行时保持原方法的未知处理，不能把空循环当作通过。

`NativeFacts` 可以直接读取电子 PDF 的文字块与位置，此时不需要表格 `PDF_SPEC`。改走网格/Fluent 路径时使用上节合同。原件视觉方法通过现有运行时执行；没有固定的视觉观察时返回 `UNABLE`，不得自行增加模型调用。

## 试跑回执

宿主执行 `check()`。回执不包含审核结果。

- `{"completed":true,"artifact":"checker.py"}`：运行全部生效条款。
- `{"completed":true,"artifact":"checker.py","clauses":["条款ID"]}`：只运行这些条款。局部试跑不能固定程序。
- 修改 `resources.py`，或修改条款方法以外的共享函数后，下一次运行全部生效条款。
- 覆盖全部条款后，先看已有诊断和候选；只对未解决的依据补读。若判断符合本次条款，再次提交同一程序且不要带 `clauses`。成功结束的模型轮和相同程序一起构成固定提交；中断、预算耗尽时留下的文件只是候选。不要只为风格、注释或导入顺序重写已核对的候选。
- 修复读取或实现错误后，可以依据新的执行证据改变判定，并说明原因。业务标准仍以本次规则为准。可执行只说明程序跑完，不说明业务判断正确。
