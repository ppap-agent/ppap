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
