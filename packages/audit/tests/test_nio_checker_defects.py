"""Regression tests for verified defects in the nio checker implementations."""

from io import BytesIO
from pathlib import Path

import pytest
from audit.checker_lib.nio.control_plan_agent import ControlPlanAgent
from audit.checker_lib.nio.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from audit.checker_lib.nio.dpvr_excel_agent import DpvrExcelAgent
from audit.checker_lib.nio.msa_report_agent import MsaReportAgent
from audit.checker_lib.nio.pfmea_agent import PfmeaAgent
from audit.checker_lib.nio.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from audit.checker_lib.nio.psw_excel_agent import PswExcelAgent
from audit.checker_lib.nio.tooling_approval_agent import ToolingApprovalAgent
from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput
from audit.engine.runtime import bind_runtime
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from PIL import Image as PilImage


def saved_file(tmp_path: Path, name: str, workbook: Workbook) -> AuditInput:
    path = tmp_path / name
    workbook.save(path)
    workbook.close()
    return AuditInput(audit_id=name, file_url=str(path))


def check_workbook(tmp_path: Path, agent_class, workbook: Workbook, rule_number: int, group: GroupContextInput | None = None) -> AuditResultItem:
    agent = agent_class(
        saved_file(tmp_path, "target.xlsx", workbook),
        RuleInput(
            rule_id="confirmed-document-rules",
            rule_profile="v3",
            type=agent_class.DOC_TYPE.get_type_name(),
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, rule_number + 1)],
        ),
        group_context=group,
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


def picture() -> Image:
    data = BytesIO()
    PilImage.new("RGB", (4, 4), "red").save(data, format="PNG")
    data.seek(0)
    return Image(data)


def sheet_workbook(title: str, rows: list[list]) -> Workbook:
    workbook = Workbook()
    workbook.active.title = title
    for row in rows:
        workbook.active.append(row)
    return workbook


def as_text(workbook: Workbook, coordinate: str) -> None:
    """Keep an error literal such as #DIV/0! as plain text, as converted sheets do."""
    cell = workbook.active[coordinate]
    cell.data_type = "s"


# --- N01 尺寸检验报告 -----------------------------------------------------

DIMENSION_HEADER = ["测点号", "主要控制方向", "名义值", "上公差", "下公差", "测量偏差值", "Part1", "Centering", "Range"]


def test_n01_6_missing_marker_column_is_not_pass(tmp_path):
    workbook = sheet_workbook("测量报告", [["测点号", "类型"], ["P1", "CC"]])

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 6)

    assert result.status != "PASS"


@pytest.mark.parametrize("rule_number", [9, 11], ids=["centering", "range"])
def test_n01_9_11_error_literal_is_not_zero(tmp_path, rule_number):
    workbook = sheet_workbook("测量报告", [DIMENSION_HEADER, ["P1", "X", None, 1, -1, None, 0.2, "#DIV/0!", "#DIV/0!"]])
    as_text(workbook, "H2")
    as_text(workbook, "I2")

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, rule_number)

    assert result.status == "UNABLE"


def test_n01_9_threshold_ng_still_wins_over_unknown_row(tmp_path):
    workbook = sheet_workbook(
        "测量报告",
        [DIMENSION_HEADER, ["P1", "X", None, 1, -1, None, 0.2, "abc", 0.1], ["P2", "X", None, 1, -1, None, 0.2, 0.9, 0.1]],
    )

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 9)

    assert result.status == "NG"


def test_n01_8_non_numeric_nominal_is_not_zero(tmp_path):
    # With nominal 0 the Centering 0.1 would match exactly.
    workbook = sheet_workbook("测量报告", [DIMENSION_HEADER, ["P1", "X", "#REF!", 1, -1, None, 0.2, 0.1, 0]])
    as_text(workbook, "C2")

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 8)

    assert result.status == "UNABLE"


TARGET_SHEET = "Hole&Slot"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (["P1", "X", None, 1, -1, None, None, 0, 0], "NG"),
        (["P1", "X", None, "NA", "NA", None, 0.2, 0, 0], "UNABLE"),
        (["P1", "X", "#REF!", 1, -1, None, 0.2, 0, 0], "UNABLE"),
        (["P1", "X", None, 1, -1, None, 0.2, 0, 0], "PASS"),
        (["P1", "X", None, 1, -1, None, 1.5, 0, 0], "NG"),
    ],
    ids=["empty-deviation", "na-tolerance", "error-nominal", "within", "outside"],
)
def test_n01_7_rows_that_cannot_be_compared_do_not_pass(tmp_path, row, expected):
    workbook = sheet_workbook(TARGET_SHEET, [DIMENSION_HEADER, row])
    if row[2] == "#REF!":
        as_text(workbook, "C2")

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 7)

    assert result.status == expected


# --- N02 PFMEA ------------------------------------------------------------


def pfmea_header_workbook(company_label: str, title: str = "PFMEA") -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append([company_label, "供应商A", "车型项目：", "ES8", "零件名称：", "扰流板", "零件号：", "P001"])
    sheet.append(["制造地址：", "合肥", "PFMEA开始日期：", "2024-01-01", "PFMEA修订日期：", "2024-02-01", "跨职能团队：", "张三"])
    for _ in range(5):
        sheet.append(["过程步骤", "内容"])
    return workbook


@pytest.mark.parametrize(("label", "expected"), [("公司名称：", "PASS"), ("客户名称：", "NG")], ids=["company", "customer-only"])
def test_n02_1_customer_name_is_not_company_name(tmp_path, label, expected):
    result = check_workbook(tmp_path, PfmeaAgent, pfmea_header_workbook(label), 1)

    assert result.status == expected


def pfmea_column_workbook(header: list[str], row: list) -> Workbook:
    workbook = Workbook()
    workbook.active.title = "PFMEA"
    workbook.active.append(header)
    workbook.active.append(row)
    return workbook


@pytest.mark.parametrize(
    ("function", "expected"),
    [("内部：拧紧坐垫\n客户工厂：将座椅安装至车身\n最终用户：坐乘", "PASS"), ("客户工厂：安装内饰板", "NG")],
    ids=["three-dimensions", "only-customer-plant"],
)
def test_n02_3_customer_plant_alone_is_not_three_dimensions(tmp_path, function, expected):
    result = check_workbook(tmp_path, PfmeaAgent, pfmea_column_workbook(["过程项功能"], [function]), 3)

    assert result.status == expected


@pytest.mark.parametrize(("severity", "expected"), [(6, "PASS"), (5, "NG")], ids=["clause-example", "not-maximum"])
def test_n02_4_percentages_are_not_severity_scores(tmp_path, severity, expected):
    effect = "内部：100%产品在线返工（４）\n客户工厂：100%产品离线返工（６）\n最终用户：次级功能降低，坐垫松动异响（５）"

    result = check_workbook(tmp_path, PfmeaAgent, pfmea_column_workbook(["失效影响", "严重度"], [effect, severity]), 4)

    assert result.status == expected


def check_against_flow(tmp_path, agent_class, title: str, rows: list[list], flow_rows: list[list], rule_number: int) -> AuditResultItem:
    workbook = Workbook()
    workbook.active.title = title
    flow = Workbook()
    flow.active.title = "过程流程图"
    for sheet, sheet_rows in ((workbook.active, rows), (flow.active, flow_rows)):
        for row in sheet_rows:
            sheet.append(row)
        sheet["Z1"] = "零件号"
        sheet["AA1"] = "P001"
    related = saved_file(tmp_path, "flow.xlsx", flow)
    group = GroupContextInput(by_doc_type={"过程流程图": [related]}, by_audit_id={related.audit_id: related})
    with bind_runtime(None):
        return check_workbook(tmp_path, agent_class, workbook, rule_number, group)


def check_pfmea_against_flow(tmp_path, pfmea_rows: list[list], flow_rows: list[list], rule_number: int) -> AuditResultItem:
    return check_against_flow(tmp_path, PfmeaAgent, "PFMEA", pfmea_rows, flow_rows, rule_number)


@pytest.mark.parametrize(("step", "expected"), [("10 焊接", "PASS"), ("10 喷涂", "NG")], ids=["same-step", "same-number-other-name"])
def test_n02_6_same_operation_number_with_another_name_is_not_found(tmp_path, step, expected):
    result = check_pfmea_against_flow(tmp_path, [["过程步骤"], [step]], [["工序号", "工序名称"], ["10", "焊接"]], 6)

    assert result.status == expected


# --- N03 控制计划 -----------------------------------------------------------


def control_plan_header(data_rows: list[list]) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "控制计划"
    sheet["A1"] = "控制计划类型"
    sheet["B1"] = "□样件  □试生产  ☑生产"
    row = 2
    for label in ("控制计划编号", "联系人contact/电话phone", "日期date（编制compi.）", "日期date（修订update）", "页数（pp.）", "页码（p.）"):
        sheet.cell(row, 1, label)
        sheet.cell(row, 2, "X1")
        row += 1
    for label in (
        "版本rev.",
        "零件号partnumber",
        "零件名称partname",
        "多功能小组mulfunctionteam",
        "公司company",
        "公司代码companycode",
        "公司批准/日期",
        "其它批准/日期（如需要）",
        "顾客工程批准/日期(如需要）",
        "顾客质量批准/日期(如需要)",
    ):
        sheet.cell(row, 1, f"{label}：X1")
        row += 1
    sheet["A20"] = "零件/工序号"
    sheet["B20"] = "工序名称"
    for offset, values in enumerate(data_rows, start=21):
        for col, value in enumerate(values, start=1):
            sheet.cell(offset, col, value)
    return workbook


@pytest.mark.parametrize(("data_rows", "expected"), [([], "PASS"), ([["√", "试生产线"]], "PASS")], ids=["header-only", "mark-in-data-table"])
def test_n03_5_marks_in_the_data_table_are_not_plan_types(tmp_path, data_rows, expected):
    result = check_workbook(tmp_path, ControlPlanAgent, control_plan_header(data_rows), 7)

    assert result.status == expected


@pytest.mark.parametrize(("name", "expected"), [("焊接", "PASS"), ("喷涂", "NG")], ids=["same-step", "same-number-other-name"])
def test_n03_6_same_operation_number_with_another_name_is_not_found(tmp_path, name, expected):
    result = check_against_flow(
        tmp_path,
        ControlPlanAgent,
        "控制计划",
        [["零件/工序号", "工序名称/操作描述"], ["10", name]],
        [["工序号", "工序名称"], ["10", "焊接"]],
        8,
    )

    assert result.status == expected


# --- N07 零部件设计验证报告 -------------------------------------------------


@pytest.mark.parametrize(
    ("conclusion", "expected"),
    [("合格", "PASS"), ("PASS", "PASS"), ("不符合", "NG"), ("基本符合", "NG"), ("检验合格，详见附件", "NG")],
    ids=["exact-cn", "exact-en", "negated", "approximate", "with-extra-text"],
)
def test_n07_2_conclusion_must_match_exactly(tmp_path, conclusion, expected):
    workbook = sheet_workbook("DPVR", [["序号", "试验报告编号", "试验类型", "实际试验结果"], ["1", "R-001", "DV", conclusion]])

    result = check_workbook(tmp_path, DpvrExcelAgent, workbook, 2)

    assert result.status == expected


@pytest.mark.parametrize(("with_pictures", "expected"), [(True, "PASS"), (False, "NG")], ids=["pictures", "text-only"])
def test_n07_5_phase_text_without_pictures_is_not_a_picture(tmp_path, with_pictures, expected):
    workbook = sheet_workbook(
        "DPVR",
        [["序号", "试验报告编号", "试验类型", "实际试验结果", "试验前图片", "试验中图片", "试验后图片"], ["1", "R-001", "DV", "合格", "见图", "见图", "见图"]],
    )
    if with_pictures:
        for column in "EFG":
            workbook.active.add_image(picture(), f"{column}2")

    result = check_workbook(tmp_path, DpvrExcelAgent, workbook, 5)

    assert result.status == expected


# --- N09 测量系统分析 -------------------------------------------------------


def fill_rr_rows(sheet, rows: range) -> None:
    for row in rows:
        for col in range(3, 13):
            sheet.cell(row, col, 0.01)


@pytest.mark.parametrize(("split", "expected"), [(False, "PASS"), (True, "NG")], ids=["one-complete-table", "two-half-tables"])
def test_n09_3_counts_each_rr_table_on_its_own(tmp_path, split, expected):
    workbook = Workbook()
    first = workbook.active
    first.title = "量具重复性和再现性数据"
    second = workbook.create_sheet("量具重复性和再现性数据2")
    evaluator_rows = ((8, 10), (13, 15), (18, 20))
    for start, end in evaluator_rows:
        if split:
            # Rows 1 and 2 of each evaluator on one sheet, row 3 on the other:
            # each sheet alone is short of 90 cells, together they are 90.
            fill_rr_rows(first, range(start, end))
            fill_rr_rows(second, range(end, end + 1))
        else:
            fill_rr_rows(first, range(start, end + 1))

    result = check_workbook(tmp_path, MsaReportAgent, workbook, 3)

    assert result.status == expected


# --- N12 模具验收报告 -------------------------------------------------------


def tooling_workbook(signature: str = "张三") -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "模具验收报告"
    sheet.append(["工艺类型", "一级分总成零件名称", "模具制造供应商", "模具存放地点", "实物照片"])
    sheet.append(["注塑", "扰流板", "供应商A", "合肥", None])
    sheet.append([])
    sheet.append(["编制", signature, "审核", signature])
    return workbook


ELSEWHERE = tuple(f"Z{row}" for row in range(1, 25, 2))


@pytest.mark.parametrize(("anchors", "expected"), [(("E2",), "PASS"), (ELSEWHERE, "NG"), ((), "NG")], ids=["in-photo-column", "pictures-elsewhere", "no-picture"])
def test_n12_4_picture_must_be_in_the_photo_column(tmp_path, anchors, expected):
    workbook = tooling_workbook()
    for anchor in anchors:
        workbook.active.add_image(picture(), anchor)

    result = check_workbook(tmp_path, ToolingApprovalAgent, workbook, 4)

    assert result.status == expected


@pytest.mark.parametrize(
    ("signature", "anchors", "expected"),
    [("张三", (), "PASS"), ("管理部门负责人签名", ("B4", "D4"), "PASS"), ("管理部门负责人签名", ELSEWHERE, "NG")],
    ids=["written-name", "placeholder-with-signature-pictures", "placeholder-with-unrelated-pictures"],
)
def test_n12_5_unrelated_pictures_do_not_sign_the_report(tmp_path, signature, anchors, expected):
    workbook = tooling_workbook(signature)
    for anchor in anchors:
        workbook.active.add_image(picture(), anchor)

    result = check_workbook(tmp_path, ToolingApprovalAgent, workbook, 5)

    assert result.status == expected


# --- N14 生产系统产能评估报告 ------------------------------------------------

CAPACITY_ROW = {
    "线体/工序类别": "注塑",
    "参与测试线体数量": 1,
    "测试总时间（h）": 10,
    "净利用时间（h）": 8,
    "产出数量": 800,
    "合格品数": 760,
    "良率": 0.95,
    "单线体/工序总产出JPH": 100,
    "现有线体数": 1,
    "班次/天": 2,
    "小时/班（h）": 10,
    "每班净利用时间（h）": 8,
    "每月标准工作天数": 20,
    "累积良率": 0.95,
    "线体/工序预估月度产能": 30400,
    "SOP+1预估产能": 30000,
    "SOP+1量纲": 20000,
    "SOP+1达成风险": "低风险",
    "SOP+2预估产能": 30000,
    "SOP+2量纲": 20000,
    "SOP+2达成风险": "低风险",
    "SOP+3预估产能": 30000,
    "SOP+3量纲": 20000,
    "SOP+3达成风险": "低风险",
}


def capacity_workbook(title: str = "产能测试及评估", **changes) -> Workbook:
    row = {**CAPACITY_ROW, **changes}
    return sheet_workbook(title, [list(row), list(row.values())])


@pytest.mark.parametrize("rule_number", [4, 5, 6])
def test_n14_unreadable_cell_is_unable_not_ng(tmp_path, rule_number):
    column = {4: "合格品数", 5: "产出数量", 6: "现有线体数"}[rule_number]
    workbook = capacity_workbook(**{column: "#DIV/0!"})

    result = check_workbook(tmp_path, PlantCapacityEvaluationAgent, workbook, rule_number)

    assert result.status == "UNABLE"


@pytest.mark.parametrize(
    ("rule_number", "changes"),
    [(4, {"合格品数": "76O"}), (5, {"单线体/工序总产出JPH": 999}), (5, {"产出数量": "8OO"}), (6, {"现有线体数": "l"})],
    ids=["yield-misread", "jph-wrong", "jph-misread", "monthly-misread"],
)
def test_n14_ocr_table_rows_are_checked_not_passed(tmp_path, rule_number, changes):
    result = check_workbook(tmp_path, PlantCapacityEvaluationAgent, capacity_workbook("table_1", **changes), rule_number)

    assert result.status != "PASS"


# --- N17 零件提交保证书 -----------------------------------------------------


def check_psw(tmp_path, workbook: Workbook, rule_number: int) -> AuditResultItem:
    agent = PswExcelAgent(
        saved_file(tmp_path, "psw.xlsx", workbook),
        RuleInput(rule_id="psw", rule_profile="v3", type="零件提交保证书", rules=[{"id": str(index)} for index in range(1, 11)]),
    )
    try:
        with bind_runtime(None):
            result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


def psw_workbook(**cells) -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A1"] = "零件提交保证书"
    for address, value in cells.items():
        sheet[address] = value
    return workbook


@pytest.mark.parametrize(("number_format", "expected"), [("0.0000", "PASS"), ("General", "NG")], ids=["shown-four-places", "shown-two-places"])
def test_n17_2_weight_places_are_read_as_displayed(tmp_path, number_format, expected):
    workbook = psw_workbook(A4="重量(kg)", B4=0.12)
    workbook.active["B4"].number_format = number_format

    result = check_psw(tmp_path, workbook, 2)

    assert result.status == expected


@pytest.mark.parametrize(
    ("options", "expected"),
    [(("□批准", "☑条件批准", "□拒绝"), "PASS"), (("☑批准", "☑条件批准", "□拒绝"), "NG"), (("□批准", "□条件批准", "□拒绝"), "NG")],
    ids=["one-character-mark", "two-character-marks", "no-mark"],
)
def test_n17_8_character_marks_count_as_disposition_selection(tmp_path, options, expected):
    workbook = psw_workbook(A10="认可情况", B10=options[0], C10=options[1], D10=options[2])

    result = check_psw(tmp_path, workbook, 8)

    assert result.status == expected


def test_n17_9_unrecognised_conditional_approval_date_is_unable(tmp_path):
    workbook = psw_workbook(A10="认可情况", B10="□批准", C10="☑条件批准", D10="□拒绝")

    result = check_psw(tmp_path, workbook, 9)

    assert result.status == "UNABLE"
