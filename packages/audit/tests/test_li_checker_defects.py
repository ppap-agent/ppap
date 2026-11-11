"""Regression guards for verified li checker defects."""

from io import BytesIO
from pathlib import Path

import fitz
import pytest
from audit.checker_lib.li.control_plan_agent import ControlPlanAgent
from audit.checker_lib.li.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from audit.checker_lib.li.dpvr_excel_agent import DpvrExcelAgent
from audit.checker_lib.li.msa_report_agent import MsaReportAgent
from audit.checker_lib.li.pfmea_agent import PfmeaAgent
from audit.checker_lib.li.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from audit.checker_lib.li.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from audit.checker_lib.li.psw_excel_agent import PswExcelAgent
from audit.contracts import AuditInput, AuditResultItem, RuleInput
from openpyxl import Workbook
from openpyxl.drawing.image import Image as WorkbookImage
from PIL import Image


def check(tmp_path: Path, agent_class, workbook: Workbook, rule_number: int, *, name: str = "target.xlsx", metadata: dict | None = None) -> AuditResultItem:
    path = tmp_path / name
    workbook.save(path)
    workbook.close()
    agent = agent_class(
        AuditInput(audit_id="target", file_url=str(path), file_metadata=metadata or {}),
        RuleInput(rule_id="confirmed", rule_profile="v2", type=agent_class.DOC_TYPE.get_type_name(), rules=[]),
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


def pfmea(effect: str = "", severity: object = None) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "第五版PFMEA表格"
    sheet.append(["过程项功能", "失效影响", "严重度", "现行探测控制", "探测度"])
    sheet.append(["内部：装配 客户工厂：装车 最终用户：使用", effect, severity, "目视检查（6）", 6])
    return workbook


@pytest.mark.parametrize("rule_number", [3, 5])
@pytest.mark.parametrize(("flow_sheet", "expected"), [(None, "NG"), ("过程流程图", "UNABLE")], ids=["no-flow-chart", "flow-chart-present"])
def test_pfmea_cross_table_rules_are_unable_when_both_tables_exist(tmp_path, rule_number, flow_sheet, expected):
    workbook = pfmea("内部：返工（4）", 4)
    if flow_sheet:
        workbook.create_sheet(flow_sheet).append(["工序号", "工序名称", "特性类别"])

    result = check(tmp_path, PfmeaAgent, workbook, rule_number)

    assert result.status == expected


@pytest.mark.parametrize(("severity", "expected"), [(6, "PASS"), (100, "NG")])
def test_pfmea_percent_is_not_a_severity_score(tmp_path, severity, expected):
    effect = "内部：100%产品在线返工（４）客户工厂：100%产品离线返工（６）最终用户：次级功能降低，坐垫松动异响（５）"

    result = check(tmp_path, PfmeaAgent, pfmea(effect, severity), 6)

    assert result.status == expected


def control_plan(stage_cells: dict[str, str]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "控制计划"
    sheet["A1"] = "控制计划类型"
    for address, value in stage_cells.items():
        sheet[address] = value
    for address, value in {"A11": "零件/工序号", "B11": "工序名称", "D11": "特性", "E12": "产品", "F12": "过程", "G11": "特殊特性分类", "K11": "频率", "L11": "控制方法"}.items():
        sheet[address] = value
    for address, value in {"A15": "OP10", "B15": "装配", "E15": "外观", "G15": "[G]", "K15": "每班", "L15": "目视"}.items():
        sheet[address] = value
    return workbook


@pytest.mark.parametrize(
    ("stage_cells", "expected"),
    [({"B1": "☑试生产", "C1": "□生产"}, "NG"), ({"B1": "□试生产", "C1": "☑生产"}, "PASS")],
    ids=["only-pre-launch", "production"],
)
def test_pre_launch_does_not_count_as_production(tmp_path, stage_cells, expected):
    result = check(tmp_path, ControlPlanAgent, control_plan(stage_cells), 1)

    assert result.status == expected


def test_pre_launch_alone_is_one_control_plan_type(tmp_path):
    result = check(tmp_path, ControlPlanAgent, control_plan({"B1": "□样件", "C1": "☑试生产", "D1": "□生产"}), 6)

    assert "有且仅有一个" not in result.suggestions


def test_dimension_rule_three_without_any_checked_dimension_row_is_not_pass(tmp_path):
    workbook = Workbook()
    workbook.active.title = "检验报告"
    workbook.active.append(["序号", "检查项目", "判定标准", "检查数据", "判定结果"])
    workbook.active.append(["非尺寸"])
    workbook.active.append([1, "外观", "无划伤", "OK", "OK"])

    result = check(tmp_path, DimensionMeasurementExcelAgent, workbook, 3)

    assert result.status != "PASS"


CAPACITY_HEADERS = (
    "线体/工序类别",
    "参与测试线体数量",
    "测试总时间（h）",
    "净利用时间（h）",
    "产出数量",
    "合格品数",
    "良率",
    "单线体/工序总产出JPH",
    "现有线体数",
    "班次/天",
    "小时/班（h）",
    "每班净利用时间（h）",
    "每月标准工作天数",
    "累积良率",
    "线体/工序预估月度产能",
    "SOP+1量纲",
    "SOP+1达成风险",
    "SOP+2预估产能",
    "SOP+2量纲",
    "SOP+2达成风险",
    "SOP+3预估产能",
    "SOP+3量纲",
    "SOP+3达成风险",
)


def capacity(**overrides: object) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "产能测试及评估"
    sheet.append(["零件名称：", "管路", "零件号：", "P001", "供应商名称：", "示例", "供应商编码：", "S01"])
    sheet.append(["产能评估总体结论：", "通过"])
    sheet.append(["后续措施：", "NA"])
    sheet.append(list(CAPACITY_HEADERS))
    values = dict(zip(CAPACITY_HEADERS, ["SMT", 1, 4, 3.8, 450, 405, 0.9, 120, 1, 1, 10.5, 9.5, 26, 0.9, 26676, 26000, "低风险", 30000, 30000, "低风险", 30000, 30000, "低风险"]))
    values.update(overrides)
    sheet.append([values[name] for name in CAPACITY_HEADERS])
    return workbook


@pytest.mark.parametrize(("rule_number", "column"), [(4, "良率"), (5, "单线体/工序总产出JPH"), (6, "单线体/工序总产出JPH")])
def test_unreadable_capacity_cell_is_unable_not_ng(tmp_path, rule_number, column):
    workbook = capacity()
    cell = workbook.active.cell(5, CAPACITY_HEADERS.index(column) + 1)
    cell.value = "#N/A"
    assert cell.data_type == "e"

    result = check(tmp_path, PlantCapacityEvaluationAgent, workbook, rule_number)

    assert result.status == "UNABLE"


def flow_chart() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    sheet.append(["工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别", "制造", "运输", "存储", "检验", "返工", "报废"])
    sheet.append(["注塑", "成型", None, None, None, None, "○"])
    sheet.append(["检验", "外观检查", None, None, None, None, None, None, None, "◇"])
    return workbook


def test_flow_chart_code_field_must_not_be_empty(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "过程流程图"
    for label, value in [("零件号", "P001"), ("零件名称", "part"), ("项目车型", "model"), ("供应商名称", "supplier"), ("代码", None), ("编制日期", "2026-01-01"), ("版本", "A"), ("团队", "team")]:
        sheet.append([label, value])

    result = check(tmp_path, ProcessFlowChartExcelAgent, workbook, 1)

    assert result.status == "NG"
    assert "代码字段" in result.suggestions


@pytest.mark.parametrize(("source_suffix", "expected"), [(".xlsx", "NG"), (".xls", "UNABLE")])
def test_flow_chart_connectors_that_cannot_be_read_are_unable(tmp_path, source_suffix, expected):
    path = tmp_path / "flow.xlsx"
    workbook = flow_chart()
    workbook.save(path)
    workbook.close()
    agent = ProcessFlowChartExcelAgent(AuditInput(audit_id="flow", file_url=str(path)), RuleInput(rule_id="confirmed", rule_profile="v2", type="过程流程图", rules=[]))
    agent._source_path = str(path.with_suffix(source_suffix)) if source_suffix != ".xlsx" else agent._source_path
    try:
        result = agent.check(5)
    finally:
        agent.close()

    assert result.status == expected


def psw(disposition: tuple[str, str, str], *, date_text: str | None = None) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A20"] = "提交结果"
    for column, label in enumerate(("尺寸测量", "外观标准", "材料和功能试验", "统计过程数据"), 1):
        sheet.cell(21, column, "☑" + label)
    sheet["A23"] = "这些结果均满足所有图纸和规范要求"
    sheet["B23"] = "☑是"
    sheet["C23"] = "☐否"
    sheet["A60"] = "认可情况"
    sheet["B60"], sheet["C60"], sheet["D60"] = disposition
    if date_text is not None:
        sheet["L62"] = "日期"
        sheet["M62"] = date_text
    return workbook


def check_psw(tmp_path, monkeypatch, workbook: Workbook, rule_number: int) -> AuditResultItem:
    from audit.engine.rule_engine.excel_ops import DefaultExcelOps
    from audit.engine.runtime import bind_runtime

    monkeypatch.setattr(DefaultExcelOps, "get_checkbox_controls", lambda *args, **kwargs: [])
    with bind_runtime(None):
        return check(tmp_path, PswExcelAgent, workbook, rule_number)


def test_checked_glyph_counts_as_the_disposition_selection(tmp_path, monkeypatch):
    result = check_psw(tmp_path, monkeypatch, psw(("☑批准", "☐条件批准", "☐拒绝")), 8)

    assert result.status == "PASS"


def test_two_checked_disposition_glyphs_are_not_one_selection(tmp_path, monkeypatch):
    result = check_psw(tmp_path, monkeypatch, psw(("☑批准", "☑条件批准", "☐拒绝")), 8)

    assert result.status == "NG"


def test_unrecognised_conditional_approval_date_is_unable(tmp_path, monkeypatch):
    result = check_psw(tmp_path, monkeypatch, psw(("☐批准", "☑条件批准", "☐拒绝")), 9)

    assert result.status == "UNABLE"


@pytest.mark.parametrize(("value", "number_format", "expected"), [(0.12, "0.0000", "PASS"), (0.12, "General", "NG"), (0.1234, "General", "PASS"), (0.12345, "0.00", "NG")])
def test_weight_decimals_follow_the_displayed_value(tmp_path, value, number_format, expected):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A4"] = "重量(kg)"
    sheet["B4"] = value
    sheet["B4"].number_format = number_format

    result = check(tmp_path, PswExcelAgent, workbook, 2)

    assert result.status == expected


def test_rr_cells_are_not_summed_across_tables(tmp_path):
    workbook = Workbook()
    first = workbook.active
    first.title = "量具重复性和再现性R&R分析报告1"
    second = workbook.create_sheet("量具重复性和再现性R&R分析报告2")
    for sheet, rows in ((first, (8, 9, 10, 13)), (second, (13, 18, 19, 20))):
        for row in rows:
            for column in range(3, 13):
                sheet.cell(row, column, 1.0)
    for sheet in (first, second):
        for row in (14, 15) if sheet is first else (14, 15):
            for column in range(3, 8):
                sheet.cell(row, column, 1.0)

    result = check(tmp_path, MsaReportAgent, workbook, 3)

    assert result.status == "NG"


def dpvr(result_text: str) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "试验报告"
    sheet.append(["序号", "试验报告编号", "试验类型", "实际试验结果"])
    sheet.append([1, "R-001", "DV", result_text])
    return workbook


@pytest.mark.parametrize(
    ("result_text", "expected"),
    [("不符合", "NG"), ("基本符合", "NG"), ("检验合格，详见附件", "NG"), ("F", "NG"), ("合格", "PASS"), ("符合", "PASS"), ("通过", "PASS"), ("P", "PASS"), ("PASS", "PASS")],
)
def test_dpvr_conclusion_must_be_exactly_an_accepted_word(tmp_path, result_text, expected):
    result = check(tmp_path, DpvrExcelAgent, dpvr(result_text), 2)

    assert result.status == expected


def picture() -> WorkbookImage:
    encoded = BytesIO()
    Image.new("RGB", (40, 40), "gray").save(encoded, format="PNG")
    return WorkbookImage(encoded)


@pytest.mark.parametrize("with_picture", [False, True])
def test_dpvr_phase_labels_need_pictures(tmp_path, with_picture):
    workbook = dpvr("合格")
    sheet = workbook.active
    sheet["A5"], sheet["C5"], sheet["E5"] = "试验前", "试验中", "试验后"
    if with_picture:
        sheet.add_image(picture(), "A6")

    result = check(tmp_path, DpvrExcelAgent, workbook, 5)

    assert result.status == ("PASS" if with_picture else "NG")


@pytest.mark.parametrize("with_picture", [False, True])
def test_dpvr_phase_picture_columns_need_anchored_pictures(tmp_path, with_picture):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "试验报告"
    sheet.append(["序号", "试验报告编号", "测试前照片", "测试中照片", "测试后照片"])
    sheet.append([1, "R-001", "见图", "见图", "见图"])
    if with_picture:
        for address in ("C2", "D2", "E2"):
            sheet.add_image(picture(), address)

    result = check(tmp_path, DpvrExcelAgent, workbook, 5)

    assert result.status == ("PASS" if with_picture else "NG")


@pytest.mark.parametrize("with_picture", [False, True])
def test_dpvr_pdf_phase_labels_need_pictures_on_the_original_page(tmp_path, with_picture):
    source = tmp_path / "report.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 60), "试验前 试验中 试验后", fontname="china-s", fontsize=10)
    if with_picture:
        encoded = BytesIO()
        Image.new("RGB", (40, 40), "gray").save(encoded, format="PNG")
        page.insert_image(fitz.Rect(40, 80, 120, 160), stream=encoded.getvalue())
    document.save(source)
    document.close()
    workbook = dpvr("合格")
    workbook.active["A5"], workbook.active["C5"], workbook.active["E5"] = "试验前", "试验中", "试验后"

    result = check(tmp_path, DpvrExcelAgent, workbook, 5, metadata={"source_pdf_url": str(source)})

    assert result.status == ("PASS" if with_picture else "NG")


def test_unparseable_dimension_standard_is_unable_not_ng():
    from audit.engine.checkers.dimension_measurement_policy import check_tolerance

    assert check_tolerance("见图纸", (10,), "OK", "卡尺").status == "UNABLE"


@pytest.mark.parametrize(("result_text", "expected"), [("OK", "NA"), ("NG", "NG")])
def test_nominal_only_dimension_row_still_checks_the_result(result_text, expected):
    from audit.engine.checkers.dimension_measurement_policy import check_tolerance

    assert check_tolerance("2X23.5", (23.5,), result_text, "卡尺").status == expected


def dpvr_pdf(tmp_path: Path, text: str) -> str:
    source = tmp_path / "report.pdf"
    document = fitz.open()
    document.new_page().insert_text((40, 60), text, fontname="china-s", fontsize=10)
    document.save(source)
    document.close()
    return str(source)


def test_dpvr_pdf_part_or_order_number_is_not_a_report_number(tmp_path):
    workbook = dpvr("合格")
    workbook.active["B2"] = None
    source = dpvr_pdf(tmp_path, "订单编号 Purchase\n样品编号 NJ23C80003")

    result = check(tmp_path, DpvrExcelAgent, workbook, 1, metadata={"source_pdf_url": source})

    assert result.status == "NG"


def test_dpvr_pdf_report_number_with_english_label_is_read(tmp_path):
    workbook = dpvr("合格")
    workbook.active["B2"] = None
    source = dpvr_pdf(tmp_path, "报告编号(Report No).: E-NJ24010196A0")

    result = check(tmp_path, DpvrExcelAgent, workbook, 1, metadata={"source_pdf_url": source})

    assert result.status == "PASS"


@pytest.mark.parametrize(("value", "expected"), [("DV/PV", "NG"), ("DV试验", "NG"), ("PV 阶段", "PASS"), ("\n PV ", "PASS")])
def test_dpvr_pdf_test_type_reads_the_whole_value(tmp_path, value, expected):
    source = dpvr_pdf(tmp_path, f"试验类型Test type：{value}\n客户名称：AB")

    result = check(tmp_path, DpvrExcelAgent, dpvr("合格"), 3, metadata={"source_pdf_url": source})

    assert result.status == expected


def test_dpvr_empty_labelled_conclusion_is_not_ignored(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "试验报告"
    sheet["A1"], sheet["B1"] = "判定结果", "合格"
    sheet["A3"] = "结论"

    result = check(tmp_path, DpvrExcelAgent, workbook, 2)

    assert result.status == "NG"


def msa_summary(first: str | None, second: str | None) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "汇总任务页"
    sheet["A3"], sheet["B3"], sheet["C3"] = "任务项一\n测量系统分析计划", "是否完成", "测量系统分析计划"
    sheet["A5"], sheet["B5"], sheet["C5"] = "任务项二\n测量系统分析报告", "是否完成", "数据类型"
    sheet.merge_cells("A3:A4")
    sheet.merge_cells("A5:A6")
    sheet["B4"], sheet["B6"] = first, second
    workbook.create_sheet("测量系统分析计划").append(["序号", "零件名称"])
    return workbook


@pytest.mark.parametrize(("first", "second", "expected"), [("OK", "OK", "PASS"), ("NOK", "OK", "NG"), (None, None, "NG")])
def test_msa_template_summary_sheet_is_read(tmp_path, first, second, expected):
    result = check(tmp_path, MsaReportAgent, msa_summary(first, second), 1)

    assert result.status == expected


def test_every_rr_table_must_be_complete(tmp_path):
    workbook = Workbook()
    complete = workbook.active
    complete.title = "量具重复性和再现性R&R分析报告1"
    partial = workbook.create_sheet("量具重复性和再现性R&R分析报告2")
    for row in (8, 9, 10, 13, 14, 15, 18, 19, 20):
        for column in range(3, 13):
            complete.cell(row, column, 1.0)
    partial.cell(8, 3, 1.0)

    result = check(tmp_path, MsaReportAgent, workbook, 3)

    assert result.status == "NG"


@pytest.mark.parametrize(
    ("effect", "severity", "expected"),
    [
        ("内部：返工（4）客户工厂：停线（6）最终用户：异响（5）", 6, "PASS"),
        ("内部：返工（4）客户工厂：停线 最终用户：异响（5）（6）", 6, "NG"),
        ("制造装配：返工（7）主机厂：停线（5） : 7", 7, "NG"),
        ("内部：返工≤1mm（4）客户工厂：停线（6）最终用户：异响（5）", 6, "PASS"),
    ],
    ids=["paired", "dimension-without-score", "two-dimensions-and-loose-number", "number-in-description"],
)
def test_pfmea_severity_scores_belong_to_each_dimension(tmp_path, effect, severity, expected):
    result = check(tmp_path, PfmeaAgent, pfmea(effect, severity), 6)

    assert result.status == expected


def pfmea_detection(control: str, detection: object) -> Workbook:
    workbook = pfmea("内部：返工（4）客户工厂：停线（6）最终用户：异响（5）", 6)
    workbook.active["D2"], workbook.active["E2"] = control, detection
    return workbook


@pytest.mark.parametrize(
    ("control", "detection", "expected"),
    [
        ("本工位的双感官100%检查（6）终检工位的100%目视检查（8）", 6, "PASS"),
        ("1.目视检查（6）2、人员核对", 6, "NG"),
        ("每小时抽检5根: 7/5", 5, "NG"),
        ("每小时抽检5根（7）", 7, "PASS"),
    ],
    ids=["paired", "measure-without-score", "loose-numbers", "number-in-measure"],
)
def test_pfmea_detection_scores_belong_to_each_measure(tmp_path, control, detection, expected):
    result = check(tmp_path, PfmeaAgent, pfmea_detection(control, detection), 7)

    assert result.status == expected


def li_control_plan(**values: str) -> Workbook:
    """The 理想 control plan template header; a value written into a label cell follows the label."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "控制计划"
    header = {
        "A3": "控制计划类型",
        "B3": "样件 sample  □     试生产 pre-launch □     生产 production ■",
        "G3": "日期date（编制compi.）",
        "I3": "2025/1/1",
        "J3": "版本Rev.：A/1",
        "M3": "页数（pp.）",
        "N3": "3",
        "A4": "控制计划编号",
        "B4": "CP-01",
        "C4": "联系人contact/电话phone",
        "E4": "张三 123",
        "G4": "日期date（修订update）",
        "I4": "2025/2/1",
        "M4": "页码（p.）",
        "N4": "1",
        "A5": "零件号part number: P001",
        "C5": "多功能小组 Mulfunction team：张三、李四",
        "G5": "公司批准/日期\ncompary appr./date：王五 2025/1/1",
        "J5": "顾客工程批准/日期(如需要）\nCustomer Engneering appro./date(if need)：/",
        "A7": "零件名称part name: 管路",
        "G7": "其它批准/日期（如需要）\nother appro./date(if need)：/",
        "J7": "顾客质量批准/日期(如需要)\nCustomer quality appro./date(if need)：/",
        "A9": "公司 company：示例公司",
        "G9": "公司代码 company code：S01",
        "J9": "其它批准/日期(如需要)\nother appro./date(if need)：/",
    }
    header.update(values)
    for address, value in header.items():
        sheet[address] = value or None
    for merged in ("A5:B6", "A7:B8", "A9:F10", "B3:F3", "C4:D4", "C5:F8", "E4:F4", "G3:H3", "G4:H4", "G5:I6", "G7:I8", "G9:I10", "J3:L4", "J5:N6", "J7:N8", "J9:N10"):
        sheet.merge_cells(merged)
    columns = {"A11": "零件/工序号", "B11": "工序名称/操作描述", "D11": "特性", "E12": "产品", "F12": "过程", "G11": "特殊特性分类", "K11": "频率", "L11": "控制方法", "M11": "反应计划"}
    for address, value in columns.items():
        sheet[address] = value
    for address, value in {"A15": "OP10", "B15": "装配", "E15": "外观", "G15": "[G]", "K15": "每班", "L15": "目视"}.items():
        sheet[address] = value
    return workbook


def test_li_control_plan_template_header_filled_inline_passes(tmp_path):
    result = check(tmp_path, ControlPlanAgent, li_control_plan(), 6)

    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize(
    "values",
    [
        {"G7": "其它批准/日期（如需要）\nother appro./date(if need)"},
        {"J9": "其它批准/日期(如需要)\nother appro./date(if need)"},
        {"G5": "公司批准/日期\ncompary appr./date"},
        {"A9": "公司 company"},
        {"I3": None},
    ],
    ids=["other-approval-empty", "second-other-approval-empty", "company-approval-empty", "company-empty", "compile-date-borrows-next-label"],
)
def test_li_control_plan_empty_header_field_is_ng(tmp_path, values):
    result = check(tmp_path, ControlPlanAgent, li_control_plan(**values), 6)

    assert result.status == "NG"


def test_approval_text_is_not_the_pp_stage(tmp_path):
    workbook = li_control_plan(G5="公司批准/日期\ncompary appr./date：王五 2025/1/1 ✓")

    result = check(tmp_path, ControlPlanAgent, workbook, 1)

    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize(
    ("status", "negative"),
    [
        ("无（√）/合格（ ）/不合格（ ）", False),
        ("无（ ）/合格（√）/不合格（ ）", False),
        ("无（ ）/合格（ ）/不合格（√）", True),
        ("无()/合格()/不合格( √ )", True),
        ("无（ ）/完成（ ）/未完成（√）", True),
        ("☑不合格", True),
    ],
)
def test_fixture_tick_belongs_to_its_option(status, negative):
    from audit.checker_lib.li.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent

    assert CheckingFixtureApprovalExcelAgent._contains_negative_checked_mark(None, status) is negative


def merged_anchor_rows(tmp_path: Path, agent_class, sheet_title: str, section_name: str, headers: list[str], *, merge: str, rows: list[list[object]], canonical: str = "") -> list[int]:
    from audit.engine.rule_engine import Table

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    sheet.append(headers)
    for values in rows:
        sheet.append(values)
    sheet.merge_cells(merge)
    path = tmp_path / "merged.xlsx"
    workbook.save(path)
    agent = agent_class(AuditInput(audit_id="merged", file_url=str(path)), RuleInput(rule_id="confirmed", rule_profile="v2", type=agent_class.DOC_TYPE.get_type_name(), rules=[]))
    try:
        section = Table(agent.rule_ctx(agent._load_workbook())).sheet(canonical or sheet_title).section(section_name)
        return [row.row_index for row in section.rows_from(section.columns(headers[0]))]
    finally:
        agent.close()


def test_merged_fixture_serial_checks_every_row(tmp_path):
    from audit.checker_lib.li.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent

    headers = ["序号", "零件号", "零件名称", "检具号", "检具名称", "检具数量", "备注"]
    rows = [[1, "P1", "管", "G1", "检具", 1], [None, "P2", "管", "G2", "检具", 1]]

    assert merged_anchor_rows(tmp_path, CheckingFixtureApprovalExcelAgent, "检具清单", "检具清单数据区", headers, merge="A2:A3", rows=rows) == [2, 3]


def test_merged_flow_process_checks_every_row(tmp_path):
    headers = ["工序名称", "操作描述", "产品特殊特性", "过程特殊特性"]
    rows = [["注塑", "成型", "外观", "温度"], [None, "取件", None, None]]

    assert merged_anchor_rows(tmp_path, ProcessFlowChartExcelAgent, "过程流程图", "过程流程主数据区", headers, merge="A2:A3", rows=rows) == [2, 3]


def test_merged_special_characteristic_step_checks_every_row(tmp_path):
    from audit.checker_lib.li.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent

    headers = ["过程/流程步骤", "序号", "零件编号", "零件名称", "特性分类", "特性描述"]
    rows = [["装配", 1, "P1", "管", "产品", "长度"], [None, 2, None, "管", "产品", "宽度"]]

    assert merged_anchor_rows(tmp_path, ProcessSpecialCharacteristicAgent, "过程特殊特性清单", "主数据区", headers, merge="A2:A3", rows=rows, canonical="主表") == [2, 3]


def tooling(rows: list[list[object]], merge: str) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "模具验收报告"
    sheet.append(["工艺类型", "一级分总成零件名称", "模具制造供应商", "模具存放地点", "模具设计尺寸"])
    for values in rows:
        sheet.append(values)
    sheet.merge_cells(merge)
    return workbook


@pytest.mark.parametrize(
    ("second_size", "expected"),
    [("1000*300*5", "NG"), ("1000×300×5", "PASS")],
)
def test_merged_tooling_process_checks_every_filled_row(tmp_path, second_size, expected):
    from audit.checker_lib.li.tooling_approval_agent import ToolingApprovalAgent

    rows = [["注塑", "管", "模具厂", "仓库", "100×30×5"], [None, "盖", "模具厂", "仓库", second_size], [None, None, None, None, None]]

    result = check(tmp_path, ToolingApprovalAgent, tooling(rows, "A2:A4"), 3)

    assert result.status == expected, result.suggestions


@pytest.mark.parametrize("with_pictures", [False, True])
def test_tooling_xlsx_signature_pictures_count(tmp_path, with_pictures):
    from audit.checker_lib.li.tooling_approval_agent import ToolingApprovalAgent

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "模具验收报告"
    sheet["A1"], sheet["D1"] = "编制edit：", "审核check："
    if with_pictures:
        sheet.add_image(picture(), "B1")
        sheet.add_image(picture(), "E1")

    result = check(tmp_path, ToolingApprovalAgent, workbook, 5)

    assert result.status == ("PASS" if with_pictures else "NG")


L12_COLUMNS = ["序号", "过程/流程步骤", "部件(来料检时涉及)", "客户要求/功能要求", "特性名称", "检验岗位", "产品/过程特性分类", "名义值/规格中心值", "规格下限", "规格上限"]
L12_COLUMNS += ["T1-特性监控方法", "测量设备", "测量方式", "防错控制", "检查控制", "抽样频率", "SPC监控", "SPC线上管理计划时间", "说明（关键要求/操作参数等）"]


def test_special_characteristics_without_any_step_row_is_ng(tmp_path):
    from audit.checker_lib.li.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent

    workbook = Workbook()
    workbook.active.title = "过程特殊特性清单"
    workbook.active.append(L12_COLUMNS)
    workbook.active.append([1])

    result = check(tmp_path, ProcessSpecialCharacteristicAgent, workbook, 2)

    assert result.status == "NG"


def equipment(*rows: list[object]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "设备开发验收清单"
    sheet["A3"], sheet["J3"] = "设备开发", "设备验收"
    sheet.append([])
    sheet.append(["序号", "产线/工序", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间", "实际到位时间", "验收状态", "验收结果", "整改措施", "整改计划"])
    sheet.delete_rows(4)
    for values in rows:
        sheet.append(values)
    return workbook


def test_equipment_development_without_any_line_row_is_ng(tmp_path):
    from audit.checker_lib.li.equipment_list_agent import EquipmentListAgent

    result = check(tmp_path, EquipmentListAgent, equipment([1], [2]), 1)

    assert result.status == "NG"


def test_capacity_without_any_line_row_is_ng(tmp_path):
    workbook = capacity()
    workbook.active.delete_rows(5)

    result = check(tmp_path, PlantCapacityEvaluationAgent, workbook, 2)

    assert result.status == "NG"


def test_capacity_follow_up_is_needed_only_with_medium_or_high_risk(tmp_path):
    workbook = capacity()
    workbook.active["B3"] = None

    result = check(tmp_path, PlantCapacityEvaluationAgent, workbook, 3)

    assert result.status == "PASS", result.suggestions


def xir(statuses: tuple[object, object, object, object]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "项目问题清单汇总"
    sheet["C5"], sheet["E5"], sheet["G5"], sheet["I5"] = "过程审核问题是否完全关闭？", "检验体系审核问题是否完全关闭？", "供应商追溯体系评审问题是否完全关闭？", "其他专项问题"
    sheet["C6"], sheet["E6"], sheet["G6"], sheet["I6"] = statuses
    sheet["B7"], sheet["C7"], sheet["E7"], sheet["G7"], sheet["H7"], sheet["I7"] = "序号", "问题描述", "对应措施", "时间节点", "负责人", "整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险"
    sheet["B8"] = 1
    return workbook


@pytest.mark.parametrize(("statuses", "expected"), [((None, None, None, None), "NG"), (("YES", "YES", "YES", "YES"), "PASS")])
def test_issue_list_status_fields_must_be_filled(tmp_path, statuses, expected):
    from audit.checker_lib.li.xir_assembly_issue_agent import XirAssemblyIssueAgent

    result = check(tmp_path, XirAssemblyIssueAgent, xir(statuses), 2)

    assert result.status == expected, result.suggestions


@pytest.mark.parametrize(("text", "count"), [("2099/12/31", 1), ("2099-1-5", 1), ("2099.01.05", 1), ("2099年1月5日", 1), ("2099/13/40", 0), ("XXXX/XX/XX", 0)])
def test_issue_deadline_date_formats(text, count):
    from audit.checker_lib.li.xir_assembly_issue_agent import _parse_deadline_dates

    assert len(_parse_deadline_dates(text)) == count


def annual_tests(rows: dict[int, list[object]]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "表格"
    sheet["A7"], sheet["C7"], sheet["D7"], sheet["E7"], sheet["G7"] = "供应商代码-全称", "零件/总成名称", "零件/总成编号", "质量等级", "车型"
    sheet["A8"], sheet["E8"], sheet["G8"] = "3DA-示例公司", "关键件", "W01"
    sheet["A9"] = "试验要求栏"
    for column, label in enumerate(("序号", "试验项目", "试验要求", "试验标准/方法", "样本数量", "测试频率", "备注"), 1):
        sheet.cell(10, column, label)
    for row, values in rows.items():
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value)
    sheet["A21"] = "签署栏"
    sheet["A22"], sheet["C22"], sheet["D22"], sheet["E22"] = "TPM/日期", "产品工程师/日期", "SVE/日期", "其他相关方/日期"
    return workbook


GOOD_TEST_ROW = [1, "盐雾试验", "48h", "GB/T 10125", "3", "每年"]


@pytest.mark.parametrize(
    ("rule_number", "later_row"),
    [(2, [2, "盐雾试验", "48h", "GB/T 10125", "3", "每两年"]), (5, [2, "盐雾试验", None, "GB/T 10125", "3", "每年"]), (4, [2, "盐雾试验", "48h", "GB/T 10125", "若干", "每年"])],
    ids=["frequency", "requirements", "sample-quantity"],
)
def test_annual_test_rows_after_a_gap_are_checked(tmp_path, rule_number, later_row):
    from audit.checker_lib.li.annual_test_requirements_agent import AnnualTestRequirementsAgent

    good = check(tmp_path, AnnualTestRequirementsAgent, annual_tests({11: GOOD_TEST_ROW, 20: GOOD_TEST_ROW}), rule_number)
    gap = check(tmp_path, AnnualTestRequirementsAgent, annual_tests({11: GOOD_TEST_ROW, 20: later_row}), rule_number)

    assert (good.status, gap.status) == ("PASS", "NG"), (good.suggestions, gap.suggestions)


def test_blank_psw_purchasing_does_not_borrow_the_design_value(tmp_path, monkeypatch):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PSW"
    for address, value in {"C19": "街道地址", "D19": "某路1号", "K19": "设计开发", "N19": "采购工程", "Q19": "PTC", "K20": "DRE", "L20": "张三", "N20": "PUR", "Q20": "TPM", "R20": "王五"}.items():
        sheet[address] = value
    sheet.merge_cells("L20:M20")
    sheet.merge_cells("O20:P20")
    monkeypatch.setenv("PPAP_NG_TOPN", "50")

    result = check_psw(tmp_path, monkeypatch, workbook, 6)

    assert "采购工程" in result.suggestions
    assert "设计开发" not in result.suggestions and "PTC" not in result.suggestions


def test_blank_psw_comments_do_not_borrow_nearby_text(tmp_path, monkeypatch):
    workbook = psw(("☐批准", "☑条件批准", "☐拒绝"), date_text="2099/1/1")
    sheet = workbook.active
    sheet["C79"], sheet["C80"], sheet["C81"] = "补充评论：", "Comments", "以上内容已确认"

    result = check_psw(tmp_path, monkeypatch, workbook, 10)

    assert result.status != "PASS"


def test_psw_without_any_deliverable_row_is_ng(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "生产件批准提交清单"
    sheet.append(["序号", "项目", "确认方法", "交付形式", "附件"])

    result = check(tmp_path, PswExcelAgent, workbook, 1)

    assert result.status == "NG"


@pytest.mark.parametrize(("approval_date", "expected"), [("", "UNABLE"), ("见附件", "UNABLE"), ("2026-02-30", "NG"), ("2026-09-30", "PASS")])
def test_psw_pdf_conditional_date_matches_the_excel_path(monkeypatch, approval_date, expected):
    from audit.checker_lib.li.psw_pdf_agent import PswPDFAgent
    from audit.engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin
    from audit.engine.checkers.psw_visual_evidence import decide_disposition_visual_rule

    observation = {"disposition_visible": True, "approved_checked": False, "conditional_checked": True, "rejected_checked": False, "approval_date": approval_date}
    status, suggestions = decide_disposition_visual_rule(9, observation, today=__import__("datetime").date(2026, 10, 2))
    decided = AuditResultItem(classification="PSW", item="条件批准日期检查", priority="P1", status=status, suggestions=suggestions)
    monkeypatch.setattr(PswPdfEvidenceMixin, "_run_fact_rule", lambda self, ith, rule_no: decided)

    result = PswPDFAgent._run_fact_rule(object.__new__(PswPDFAgent), 8, 9)

    assert result.status == expected


def test_li_control_plan_header_version_is_not_the_revision_history_label(tmp_path):
    workbook = li_control_plan()
    workbook.active["A31"] = "版本\nRev."

    result = check(tmp_path, ControlPlanAgent, workbook, 6)

    assert result.status == "PASS", result.suggestions


SUPPLIER_HEADER = ["零件号", "单件重量", "材料执行标准", "材料类型", "材料牌号", "材料规格/尺寸", "材料供应商名称", "通过何种质量管理体系认证", "自制", "外协-国产", "外协-进口", "领用件", "完成PPAP"]


@pytest.mark.parametrize(
    ("marks", "expected"),
    [
        (("×", None, None, None), "PASS"),
        (("☒", "N", "N", "N"), "PASS"),
        (("Y", "否", "/", "-"), "PASS"),
        (("—", "no", "n/a", "√"), "PASS"),
        (("√", "×", None, None), "NG"),
        (("N", "N", "N", "NA"), "NG"),
    ],
    ids=["cross", "box-and-n", "y-and-placeholders", "dash-no-na", "two-selected", "none-selected"],
)
def test_supplier_status_n_and_placeholders_are_not_a_selection(tmp_path, marks, expected):
    from audit.checker_lib.li.sub_supplier_list_excel_agent import SubSupplierListExcelAgent

    workbook = Workbook()
    workbook.active.title = "材料清单"
    workbook.active.append(SUPPLIER_HEADER)
    workbook.active.append(["P001", "0.1200", "GB", "PP", "PP-T20", "2mm", "供应商A", "IATF16949", *marks, "Y"])

    result = check(tmp_path, SubSupplierListExcelAgent, workbook, 3)

    assert result.status == expected, result.suggestions


def ccsc(*rows: list[object]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "产品过程CCSC特性管理清单"
    sheet.append(["工序流程编号", "P阶段Cpk结果", "P阶段结果判定"])
    for values in rows:
        sheet.append(values)
    return workbook


@pytest.mark.parametrize("rule_number", [1, 2])
def test_ccsc_rows_numbered_na_are_skipped(tmp_path, rule_number):
    from audit.checker_lib.li.initial_process_capacity_agent import InitialProcessCapacityAgent

    result = check(tmp_path, InitialProcessCapacityAgent, ccsc(["OP10", 1.5, "达标"], ["NA", 0.5, "未达标"]), rule_number)

    assert result.status == "PASS", result.suggestions


@pytest.mark.parametrize("rule_number", [1, 2])
def test_ccsc_rows_after_a_blank_number_are_checked(tmp_path, rule_number):
    from audit.checker_lib.li.initial_process_capacity_agent import InitialProcessCapacityAgent

    result = check(tmp_path, InitialProcessCapacityAgent, ccsc(["OP10", 1.5, "达标"], [None, None, None], ["OP30", 0.5, "未达标"]), rule_number)

    assert result.status == "NG", result.suggestions


@pytest.mark.parametrize(("step", "risk", "expected"), [("装配", "高风险", "PASS"), (None, "高风险", "NG"), (None, "低风险", "PASS")])
def test_high_risk_row_without_step_is_checked(tmp_path, step, risk, expected):
    from audit.checker_lib.li.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent

    workbook = Workbook()
    workbook.active.title = "过程特殊特性清单"
    workbook.active.append(["序号", "过程/流程步骤", "现有管理方式风险评估", "高风险管理项预防措施"])
    workbook.active.append([1, "焊接", "低风险", None])
    workbook.active.append([2, step, risk, "防错"])

    result = check(tmp_path, ProcessSpecialCharacteristicAgent, workbook, 3)

    assert result.status == expected, result.suggestions


@pytest.mark.parametrize(("rule_number", "orphan"), [(1, [3, None, None, "供应商B"]), (2, [3, None, None, None, None, None, None, None, None, None, None, "通过"])])
def test_equipment_row_with_content_in_any_column_is_a_data_row(tmp_path, rule_number, orphan):
    from audit.checker_lib.li.equipment_list_agent import EquipmentListAgent

    full = [1, "L1", "设备A", "供应商A", "M1", 1, 0, "通用", "2026-01-01", "2026-01-01", "已验收", "通过", None, None]

    result = check(tmp_path, EquipmentListAgent, equipment(full, orphan), rule_number)

    assert result.status == "NG", result.suggestions


@pytest.mark.parametrize("header", ["整车/总成功/性能性、SOP+3产能风险、安全性、法律法规风险", "整车/总成/性能性、SOP+3产能风险、安全性、法律法规风险"])
@pytest.mark.parametrize(("answer", "expected"), [("Yes", "PASS"), ("待定", "NG")])
def test_issue_risk_header_with_either_wording(tmp_path, header, answer, expected):
    from audit.checker_lib.li.xir_assembly_issue_agent import XirAssemblyIssueAgent

    workbook = xir(("YES", "YES", "YES", "YES"))
    workbook.active["I7"] = header
    workbook.active["C8"], workbook.active["I8"] = "装配异响", answer

    result = check(tmp_path, XirAssemblyIssueAgent, workbook, 4)

    assert result.status == expected, result.suggestions


def test_flow_process_with_six_empty_columns_fails_even_under_a_connector(tmp_path, monkeypatch):
    workbook = flow_chart()
    workbook.active.append(["包装"])
    workbook.active.append(["入库", None, None, None, None, None, None, None, "▽"])
    monkeypatch.setattr(ProcessFlowChartExcelAgent, "_rows_with_connector_evidence", lambda self, ws, **_: set(range(1, 20)))

    result = check(tmp_path, ProcessFlowChartExcelAgent, workbook, 6)

    assert result.status == "NG", result.suggestions
