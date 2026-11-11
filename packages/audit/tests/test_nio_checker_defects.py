"""Regression tests for verified defects in the nio checker implementations."""

from io import BytesIO
from pathlib import Path

import pytest
from audit.checker_lib.nio.annual_test_requirements_agent import AnnualTestRequirementsAgent
from audit.checker_lib.nio.checking_fixture_approval_excel_agent import CheckingFixtureApprovalExcelAgent
from audit.checker_lib.nio.control_plan_agent import ControlPlanAgent
from audit.checker_lib.nio.dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from audit.checker_lib.nio.dpvr_excel_agent import DpvrExcelAgent
from audit.checker_lib.nio.dvp_r_excel_agent import DvpRExcelAgent
from audit.checker_lib.nio.equipment_list_agent import EquipmentListAgent
from audit.checker_lib.nio.initial_process_capacity_agent import InitialProcessCapacityAgent
from audit.checker_lib.nio.msa_report_agent import MsaReportAgent
from audit.checker_lib.nio.pfmea_agent import PfmeaAgent
from audit.checker_lib.nio.plant_capacity_evaluation_agent import PlantCapacityEvaluationAgent
from audit.checker_lib.nio.process_flow_chart_excel_agent import ProcessFlowChartExcelAgent
from audit.checker_lib.nio.process_special_characteristic_agent import ProcessSpecialCharacteristicAgent
from audit.checker_lib.nio.psw_excel_agent import PswExcelAgent
from audit.checker_lib.nio.sub_supplier_list_excel_agent import SubSupplierListExcelAgent
from audit.checker_lib.nio.tooling_approval_agent import ToolingApprovalAgent
from audit.checker_lib.nio.xir_assembly_issue_agent import XirAssemblyIssueAgent
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
        (["P1", "X", None, "NA", "NA", None, 0.2, 0, 0], "PASS"),
        (["P1", "X", None, None, None, None, 0.2, 0, 0], "NG"),
        (["P1", "X", "#REF!", 1, -1, None, 0.2, 0, 0], "UNABLE"),
        (["P1", "X", None, 1, -1, None, 0.2, 0, 0], "PASS"),
        (["P1", "X", None, 1, -1, None, 1.5, 0, 0], "NG"),
    ],
    ids=["empty-deviation", "na-tolerance-has-no-range", "blank-tolerance", "error-nominal", "within", "outside"],
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


# --- Behaviors no labelled case covered (fix round 2) ----------------------

FULL_DIMENSION_HEADER = [*DIMENSION_HEADER, "坐标X", "坐标Y", "坐标Z", "类型", "测点图/截面"]


@pytest.mark.parametrize("rule_number", range(2, 12))
def test_n01_report_without_measured_rows_is_not_pass(tmp_path, rule_number):
    workbook = sheet_workbook(TARGET_SHEET, [FULL_DIMENSION_HEADER])

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, rule_number)

    assert result.status == "NG"


@pytest.mark.parametrize(("rule_number", "column"), [(8, "H2"), (10, "I2")], ids=["centering", "range"])
def test_n01_8_10_error_literal_result_is_unable(tmp_path, rule_number, column):
    workbook = sheet_workbook("测量报告", [DIMENSION_HEADER, ["P1", "X", None, 1, -1, None, 0.2, "#DIV/0!", "#DIV/0!"]])
    as_text(workbook, column)

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, rule_number)

    assert result.status == "UNABLE"


def test_n01_1_label_must_be_the_whole_cell(tmp_path):
    workbook = sheet_workbook("CoverSheet", [["零件号", "P001"], ["零件名", "扰流板"], ["项目阶段", "PPAP"], ["日期"], ["2024-01-01"]])

    result = check_workbook(tmp_path, DimensionMeasurementExcelAgent, workbook, 1)

    assert result.status == "NG"
    assert "缺少字段: 项目" in result.suggestions


def test_n02_3_merged_function_cell_covers_every_row_of_the_block(tmp_path):
    workbook = pfmea_column_workbook(["过程项功能", "失效模式"], ["内部：拧紧坐垫\n客户工厂：将座椅安装至车身\n最终用户：坐乘", "松动"])
    workbook.active.append([None, "异响"])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, PfmeaAgent, workbook, 3)

    assert result.status == "PASS"


@pytest.mark.parametrize(("measures", "expected"), [("目视检查(7);首件检验(9)", "PASS"), ("目视检查(7);首件检验", "NG")], ids=["each-scored", "one-unscored"])
def test_n02_5_every_detection_measure_needs_a_score(tmp_path, measures, expected):
    result = check_workbook(tmp_path, PfmeaAgent, pfmea_column_workbook(["现行探测控制", "探测度"], [measures, 7]), 5)

    assert result.status == expected


def two_flow_charts_group(tmp_path: Path) -> GroupContextInput:
    related = []
    for name in ("flow1.xlsx", "flow2.xlsx"):
        flow = sheet_workbook("过程流程图", [["工序号", "工序名称"], ["10", "焊接"]])
        related.append(saved_file(tmp_path, name, flow))
    return GroupContextInput(by_doc_type={"过程流程图": related}, by_audit_id={item.audit_id: item for item in related})


@pytest.mark.parametrize("rule_number", [6, 7])
def test_n02_6_7_several_flow_charts_are_unable(tmp_path, rule_number):
    workbook = sheet_workbook("PFMEA", [["过程步骤", "特殊特性"], ["10 焊接", "CC"]])

    with bind_runtime(None):
        result = check_workbook(tmp_path, PfmeaAgent, workbook, rule_number, two_flow_charts_group(tmp_path))

    assert result.status == "UNABLE"


def test_n03_1_tick_without_stage_text_is_unable(tmp_path):
    workbook = sheet_workbook("控制计划", [["控制计划类型", "√"]])

    result = check_workbook(tmp_path, ControlPlanAgent, workbook, 1)

    assert result.status == "UNABLE"


@pytest.mark.parametrize(
    ("rule_number", "rows", "merge"),
    [
        (4, [["序号", "特殊特性分类", "特性", None], [None, None, "产品", "过程"], ["1", "CC", "直径", None], ["2", None, None, None]], "B3:B4"),
        (5, [["序号", "特殊特性分类", "控制方法"], ["1", "CC", "目视"], ["2", None, None]], "B2:B3"),
    ],
    ids=["n03_2-product-process", "n03_3-control-method"],
)
def test_n03_2_3_every_row_of_a_merged_special_class_is_checked(tmp_path, rule_number, rows, merge):
    workbook = sheet_workbook("控制计划", rows)
    workbook.active.merge_cells(merge)

    result = check_workbook(tmp_path, ControlPlanAgent, workbook, rule_number)

    assert result.status == "NG"


@pytest.mark.parametrize(
    ("rule_number", "rows"),
    [
        (4, [["序号", "特殊特性分类", "特性", None], [None, None, "产品", "过程"]]),
        (5, [["序号", "特殊特性分类", "控制方法"]]),
        (6, [["序号", "频率"]]),
    ],
    ids=["n03_2", "n03_3", "n03_4"],
)
def test_n03_2_4_columns_without_detail_rows_are_not_pass(tmp_path, rule_number, rows):
    result = check_workbook(tmp_path, ControlPlanAgent, sheet_workbook("控制计划", rows), rule_number)

    assert result.status == "NG"


def several_related_group(tmp_path: Path, doc_type: str, title: str, rows: list[list]) -> GroupContextInput:
    related = []
    for index in (1, 2):
        workbook = sheet_workbook(title, rows)
        workbook.active["Z1"] = "零件号"
        workbook.active["AA1"] = "P001"
        related.append(saved_file(tmp_path, f"related{index}.xlsx", workbook))
    return GroupContextInput(by_doc_type={doc_type: related}, by_audit_id={item.audit_id: item for item in related})


@pytest.mark.parametrize(
    ("rule_number", "doc_type", "title", "rows"),
    [
        (8, "过程流程图", "过程流程图", [["工序号", "工序名称"], ["10", "焊接"]]),
        (3, "潜在过程失效模式及后果分析", "PFMEA", [["过程步骤", "特殊特性"], ["10 焊接", "CC"]]),
        (10, "潜在过程失效模式及后果分析", "PFMEA", [["过程步骤", "现行探测控制"], ["10 焊接", "目视"]]),
    ],
    ids=["n03_6-flow", "n03_7-pfmea", "n03_9-pfmea"],
)
def test_n03_6_7_9_several_reference_files_are_unable(tmp_path, rule_number, doc_type, title, rows):
    workbook = sheet_workbook("控制计划", [["零件/工序号", "工序名称/操作描述", "特殊特性分类", "评价测量技术"], ["10", "焊接", "CC", "目视"]])
    workbook.active["Z1"] = "零件号"
    workbook.active["AA1"] = "P001"

    with bind_runtime(None):
        result = check_workbook(tmp_path, ControlPlanAgent, workbook, rule_number, several_related_group(tmp_path, doc_type, title, rows))

    assert result.status == "UNABLE"


def test_n03_6_missing_part_number_is_unable_not_ng(tmp_path):
    workbook = sheet_workbook("控制计划", [["零件/工序号", "工序名称/操作描述"], ["10", "焊接"]])
    flow = saved_file(tmp_path, "flow.xlsx", sheet_workbook("过程流程图", [["工序号", "工序名称"], ["10", "焊接"]]))
    group = GroupContextInput(by_doc_type={"过程流程图": [flow]}, by_audit_id={flow.audit_id: flow})

    with bind_runtime(None):
        result = check_workbook(tmp_path, ControlPlanAgent, workbook, 8, group)

    assert result.status == "UNABLE"


def test_n03_6_cross_file_rows_read_a_vertically_merged_operation_number(tmp_path):
    workbook = sheet_workbook("控制计划", [["零件/工序号", "工序名称/操作描述"], ["10", "焊接"], [None, "打磨"]])
    workbook.active.merge_cells("A2:A3")
    flow = sheet_workbook("过程流程图", [["工序号", "工序名称"], ["10", "焊接"], ["20", "打磨"]])
    for sheet in (workbook.active, flow.active):
        sheet["Z1"] = "零件号"
        sheet["AA1"] = "P001"
    related = saved_file(tmp_path, "flow.xlsx", flow)
    group = GroupContextInput(by_doc_type={"过程流程图": [related]}, by_audit_id={related.audit_id: related})

    with bind_runtime(None):
        result = check_workbook(tmp_path, ControlPlanAgent, workbook, 8, group)

    # The control plan's 打磨 row belongs to operation 10, so the flow chart's 20 打磨 is not in it.
    assert result.status == "NG"


SCL_HEADER = ["零件编号", "零件名称", "特性分类", "特性描述", "特性值", "特性类别", "特殊特性种类", "控制方法（制造过程）", "防错方案描述", "频次", "样本数"]


def test_n04_2_every_row_of_a_merged_part_number_is_checked(tmp_path):
    full = ["P001", "支架", "产品", "孔径", "10±0.1", "尺寸", "CC", "检具", "防错销", "每班", "5"]
    inner = [None, "支架", "产品", "孔深", None, "尺寸", "CC", "检具", "防错销", "每班", "5"]
    workbook = sheet_workbook("特殊特性清单", [SCL_HEADER, full, inner])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, ProcessSpecialCharacteristicAgent, workbook, 2)

    assert result.status == "NG"


PFD_HEADER = ["工序号", "工序名称", "操作描述", "产品特殊特性", "产品特性类别", "过程特殊特性", "过程特性类别"]


@pytest.mark.parametrize("merged", [False, True], ids=["category-without-process-name", "inside-merged-process-name"])
@pytest.mark.parametrize("rule_number", [2, 3])
def test_n05_2_3_category_rows_are_checked_without_their_own_process_name(tmp_path, rule_number, merged):
    category = 4 if rule_number == 2 else 6
    first = ["10", "焊接", "点焊", "焊点强度", "CC", "电流", "SC"]
    second = ["20", None, None, None, None, None, None]
    second[category] = "SC"
    workbook = sheet_workbook("过程流程图", [PFD_HEADER, first, second])
    if merged:
        workbook.active.merge_cells("B2:B3")

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, rule_number)

    assert result.status == "NG"


def test_n05_5_every_row_of_a_merged_process_name_needs_a_symbol(tmp_path):
    workbook = sheet_workbook(
        "过程流程图",
        [["工序号", "工序名称", "制造", "运输", "存储", "检验", "返工", "报废"], ["10", "焊接", "○", None, None, None, None, None], ["11", None, None, None, None, None, None, None]],
    )
    workbook.active.merge_cells("B2:B3")

    result = check_workbook(tmp_path, ProcessFlowChartExcelAgent, workbook, 5)

    assert result.status == "NG"


DVP_R_HEADER = ["序号", "试验项目", "试验要求", "试验标准方法", "试验类型", "完成节点", "试验地点", "数量", "状态", "外观属性", "计划开始", "计划结束"]


def test_n06_2_every_row_of_a_merged_serial_is_checked(tmp_path):
    full = ["1", "盐雾", "无锈蚀", "GB/T 10125", "DV", "OTS", "实验室", "3", "OTS", "A", "2024-01-01", "2024-02-01"]
    inner = [None, "高温", None, "GB/T 2423", "DV", "OTS", "实验室", "3", "OTS", "A", "2024-01-01", "2024-02-01"]
    workbook = sheet_workbook("DVP&R", [DVP_R_HEADER, full, inner])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, DvpRExcelAgent, workbook, 2)

    assert result.status == "NG"


def test_n07_2_every_row_of_a_merged_serial_is_checked(tmp_path):
    workbook = sheet_workbook("DPVR", [["序号", "试验报告编号", "试验类型", "实际试验结果"], ["1", "R-001", "DV", "合格"], [None, "R-002", "DV", "不合格"]])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, DpvrExcelAgent, workbook, 2)

    assert result.status == "NG"


@pytest.mark.parametrize(("value", "expected"), [("R-2024-001", "PASS"), (None, "NG")], ids=["filled", "empty"])
def test_n07_1_report_level_number_decides_without_an_item_table(tmp_path, value, expected):
    workbook = sheet_workbook("DPVR", [["检测报告"], ["报告编号：", value], ["委托单位", "供应商A"]])

    result = check_workbook(tmp_path, DpvrExcelAgent, workbook, 1)

    assert result.status == expected


SUPPLIER_HEADER = ["零件号", "单件重量", "材料执行标准", "材料类型", "材料牌号", "材料规格/尺寸", "材料供应商名称", "通过何种质量管理体系认证", "自制", "外协-国产", "外协-进口", "领用件", "完成PPAP"]


def test_n08_2_every_row_of_a_merged_part_number_is_checked(tmp_path):
    full = ["P001", "0.1200", "GB", "PP", "PP-T20", "2mm", "供应商A", "IATF16949", None, "√", None, None, "Y"]
    inner = [None, "0.1200", "GB", "PP", None, "2mm", "供应商A", "IATF16949", None, "√", None, None, "Y"]
    workbook = sheet_workbook("材料清单", [SUPPLIER_HEADER, full, inner])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, SubSupplierListExcelAgent, workbook, 2)

    assert result.status == "NG"


def test_n08_3_missing_part_number_column_is_ng_not_error(tmp_path):
    header = [name for name in SUPPLIER_HEADER if name != "零件号"]
    workbook = sheet_workbook("材料清单", [["序号", *header], ["1", "0.1200", "GB", "PP", "PP-T20", "2mm", "供应商A", "IATF16949", None, "√", None, None, "Y"]])

    result = check_workbook(tmp_path, SubSupplierListExcelAgent, workbook, 3)

    assert result.status == "NG"


@pytest.mark.parametrize(
    ("marks", "expected"),
    [
        (("×", None, None, None), "PASS"),
        (("☒", "N", "N", "N"), "PASS"),
        (("Y", "否", "/", "-"), "PASS"),
        (("√", "×", None, None), "NG"),
        (("N", "N", "N", "NA"), "NG"),
    ],
    ids=["cross", "box-and-n", "y-and-placeholders", "two-selected", "none-selected"],
)
def test_n08_3_n_and_placeholders_are_not_a_selection(tmp_path, marks, expected):
    row = ["P001", "0.1200", "GB", "PP", "PP-T20", "2mm", "供应商A", "IATF16949", *marks, "Y"]
    workbook = sheet_workbook("材料清单", [SUPPLIER_HEADER, row])

    result = check_workbook(tmp_path, SubSupplierListExcelAgent, workbook, 3)

    assert result.status == expected


MSA_PLAN_HEADER = ["序号", "零件名称", "零件号", "被测参数", "基准值", "公差", "量具名称", "量具编号", "测量周期", "测量人数", "产品数量", "测量次数/人", "计划日期", "实际测量日期"]


def test_n09_2_a_named_sheet_without_the_plan_table_does_not_fail_the_plan(tmp_path):
    workbook = sheet_workbook("测量系统分析计划", [MSA_PLAN_HEADER, ["1", "支架", "P001", "孔径", "10", "±0.1", "卡尺", "G-01", "年", "3", "10", "3", "2024-01-01", "2024-01-05"]])
    workbook.create_sheet("MSA说明").append(["本文件按 MSA 第四版执行"])

    result = check_workbook(tmp_path, MsaReportAgent, workbook, 2)

    assert result.status == "PASS"


def test_n10_1_every_row_of_a_merged_process_number_is_checked(tmp_path):
    workbook = sheet_workbook("产品过程CCSC特性管理清单", [["工序流程编号", "P阶段Cpk结果", "P阶段结果判定"], ["OP10", 1.67, "达标"], [None, None, "达标"]])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, InitialProcessCapacityAgent, workbook, 1)

    assert result.status == "NG"


def fixture_report_workbook(list_rows: list[list]) -> Workbook:
    workbook = Workbook()
    report = workbook.active
    report.title = "检具认可报告"
    for label in ("零件号", "零件名", "G.D&T图号", "产品图纸更改级别", "总成号", "年型", "设计方", "制造方", "检具号", "三坐标检测机构", "检具认可负责人", "日期"):
        report.append([label, "X1"])
    report.append(["所附文件", "状态"])
    report.append(["所附文件项", "状态"])
    report.append(["尺寸报告", "√"])
    report.append(["2.结论", "认可"])
    report.append([None, "认可"])
    fixture_list = workbook.create_sheet("检具清单")
    fixture_list.append(["项目检具清单"])
    fixture_list.append(["序号", "零件号", "零件名称", "检具号", "检具名称", "检具数量", "备注"])
    for row in list_rows:
        fixture_list.append(row)
    return workbook


@pytest.mark.parametrize(("list_rows", "expected"), [([["1", "P001", "支架", "G-01", "支架检具", "1", None]], "PASS"), ([], "NG")], ids=["listed", "empty-list"])
def test_n11_1_fixture_list_without_rows_is_not_pass(tmp_path, list_rows, expected):
    result = check_workbook(tmp_path, CheckingFixtureApprovalExcelAgent, fixture_report_workbook(list_rows), 1)

    assert result.status == expected


TOOLING_HEADER = ["工艺类型", "一级分总成零件名称", "模具制造供应商", "模具存放地点", "实物照片", "实物确认"]


def test_n12_6_merged_confirmation_covers_every_row_of_the_block(tmp_path):
    workbook = sheet_workbook("模具验收报告", [TOOLING_HEADER, ["注塑", "扰流板", "供应商A", "合肥", None, "有"], ["注塑", "支架", "供应商A", "合肥", None, None]])
    workbook.active.merge_cells("F2:F3")

    result = check_workbook(tmp_path, ToolingApprovalAgent, workbook, 6)

    assert result.status == "PASS"


EQUIPMENT_HEADER = ["产线/工序", "设备开发", "设备", "供应商", "设备型号", "利旧设备数量", "新采购设备数量", "设备通用情况", "计划到位时间"]


def test_n13_1_list_without_line_rows_is_not_pass(tmp_path):
    result = check_workbook(tmp_path, EquipmentListAgent, sheet_workbook("设备验收清单", [EQUIPMENT_HEADER]), 1)

    assert result.status == "NG"


@pytest.mark.parametrize(("title", "expected"), [("产能测试及评估", "NG"), ("table_1", "UNABLE")], ids=["native", "ocr-table"])
@pytest.mark.parametrize("rule_number", [2, 4, 5, 6])
def test_n14_report_without_capacity_rows_is_not_pass(tmp_path, rule_number, title, expected):
    workbook = sheet_workbook(title, [list(CAPACITY_ROW)])

    result = check_workbook(tmp_path, PlantCapacityEvaluationAgent, workbook, rule_number)

    assert result.status == expected


def test_n14_4_nan_text_is_not_a_yield(tmp_path):
    result = check_workbook(tmp_path, PlantCapacityEvaluationAgent, capacity_workbook(良率="nan"), 4)

    assert result.status == "NG"


def test_n12_3_every_row_of_a_merged_process_type_is_checked(tmp_path):
    header = [*TOOLING_HEADER, "模具设计尺寸"]
    workbook = sheet_workbook("模具验收报告", [header, ["注塑", "扰流板", "供应商A", "合肥", None, "有", "800×600×500"], [None, "支架", "供应商A", "合肥", None, "有", "约800"]])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, ToolingApprovalAgent, workbook, 3)

    assert result.status == "NG"


def test_n14_2_every_row_of_a_merged_line_type_is_checked(tmp_path):
    inner = {**CAPACITY_ROW, "线体/工序类别": None, "产出数量": None}
    workbook = sheet_workbook("产能测试及评估", [list(CAPACITY_ROW), list(CAPACITY_ROW.values()), list(inner.values())])
    workbook.active.merge_cells("A2:A3")

    result = check_workbook(tmp_path, PlantCapacityEvaluationAgent, workbook, 2)

    assert result.status == "NG"


@pytest.mark.parametrize("deadline", ["2030/01/05", "2030-01-05", "2030.1.5", "2030/1/5"])
def test_n15_3_common_date_spellings_are_dates(tmp_path, deadline):
    workbook = sheet_workbook("问题清单", [["问题描述", "对应措施", "时间节点", "负责人", "SOP+3产能风险"], ["异响", "更换卡扣", deadline, "张三", "Yes"]])

    result = check_workbook(tmp_path, XirAssemblyIssueAgent, workbook, 3)

    assert result.status == "PASS"


@pytest.mark.parametrize("rule_number", [2, 5])
def test_n16_2_5_rows_after_a_blank_spacer_row_are_checked(tmp_path, rule_number):
    workbook = sheet_workbook(
        "表格",
        [["试验项目", "测试频率", "试验要求", "试验标准/方法"], ["盐雾", "每年", "无锈蚀", "GB/T 10125"], [], ["高温", "每月", None, None]],
    )

    result = check_workbook(tmp_path, AnnualTestRequirementsAgent, workbook, rule_number)

    assert result.status == "NG"


def test_n17_1_deliverable_list_without_items_is_not_pass(tmp_path):
    workbook = psw_workbook()
    deliverables = workbook.create_sheet("生产件批准交付物文件清单")
    deliverables.append(["项目", "附件"])

    result = check_psw(tmp_path, workbook, 1)

    assert result.status == "NG"


@pytest.mark.parametrize(
    ("value", "expected"), [("某某汽车部件有限公司/A123", "PASS"), ("某某汽车部件有限公司//A123", "NG"), ("某某汽车部件有限公司/A123/", "NG")], ids=["name-code", "double-slash", "trailing-slash"]
)
def test_n17_4_empty_segments_are_not_name_or_code(tmp_path, value, expected):
    workbook = psw_workbook(A3="供应商名称/供应商代码", A4=value)

    result = check_psw(tmp_path, workbook, 4)

    assert result.status == expected


def test_n17_9_blank_conditional_approval_date_is_ng(tmp_path):
    workbook = psw_workbook(A10="认可情况", B10="□批准", C10="☑条件批准", D10="□拒绝", P12="日期")

    result = check_psw(tmp_path, workbook, 9)

    assert result.status == "NG"


def test_n17_5_text_typed_into_the_signature_box_is_not_a_signature(tmp_path):
    from audit.checker_lib.nio.psw_excel_agent import PswExcelAgent as NioPswAgent
    from audit.contracts import AuditInput, RuleInput
    from audit.engine.runtime import bind_runtime

    path = tmp_path / "psw.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "零件提交保证书"
    sheet["A25"] = "经授权的供方代表签字"
    sheet["D25"] = "张三"
    workbook.save(path)
    agent = NioPswAgent(AuditInput(audit_id="psw-typed", file_url=str(path)), RuleInput(rule_id="psw", rule_profile="v3", type="零件提交保证书", rules=[]))
    try:
        with bind_runtime(None):
            result = agent.check(4)
        assert result.status == "NG"
        assert "签" in result.suggestions and "章" not in result.suggestions
    finally:
        agent.close()
