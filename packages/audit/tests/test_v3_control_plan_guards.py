from pathlib import Path

import pytest
from audit.checker_lib.nio.control_plan_agent import ControlPlanAgent
from audit.contracts import AuditInput, AuditResultItem, RuleInput
from openpyxl import Workbook


def control_plan() -> Workbook:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "控制计划"
    for address, value in {
        "A11": "零件/工序号",
        "B11": "工序名称",
        "D11": "特性",
        "E12": "产品",
        "F12": "过程",
        "G11": "特殊特性分类",
        "K13": "频率",
        "L11": "控制方法",
        "A15": "OP15",
        "B15": "装配",
        "E15": "外观",
        "G15": "SC",
        "K15": "连续/自检",
        "L15": "作业指导书",
    }.items():
        sheet[address] = value
    return workbook


def check_rule(tmp_path: Path, workbook: Workbook, rule_number: int, *, source_pdf_url: str | None = None) -> AuditResultItem:
    path = tmp_path / "control-plan.xlsx"
    workbook.save(path)
    workbook.close()
    agent = ControlPlanAgent(
        AuditInput(audit_id="control-plan", file_url=str(path), file_metadata={"source_pdf_url": source_pdf_url} if source_pdf_url else {}),
        RuleInput(
            rule_id="confirmed-rules",
            rule_profile="v3",
            type="控制计划",
            rules=[{"id": str(index), "category": "local_excel_check", "rule_to_check": f"规则 {index}"} for index in range(1, 12)],
        ),
    )
    try:
        result = agent.check(rule_number - 1)
        assert result is not None
        return result
    finally:
        agent.close()


@pytest.mark.parametrize("rule_number", [4, 6])
def test_merged_section_heading_does_not_fail_a_complete_detail_row(tmp_path, rule_number):
    workbook = control_plan()
    sheet = workbook.active
    sheet.merge_cells("A14:N14")
    sheet["A14"] = "部件预装"

    result = check_rule(tmp_path, workbook, rule_number)

    assert result.status == "PASS"


def test_numbered_merged_section_heading_does_not_require_frequency(tmp_path):
    workbook = control_plan()
    sheet = workbook.active
    sheet["A14"] = "4.0"
    sheet.merge_cells("B14:L14")
    sheet["B14"] = "Incoming Inspection进料检验"

    result = check_rule(tmp_path, workbook, 6)

    assert result.status == "PASS"


def test_detail_row_still_requires_frequency_after_a_section_heading(tmp_path):
    workbook = control_plan()
    sheet = workbook.active
    sheet["A14"] = "4.0"
    sheet.merge_cells("B14:L14")
    sheet["B14"] = "Incoming Inspection进料检验"
    sheet["K15"] = None

    result = check_rule(tmp_path, workbook, 6)

    assert result.status == "NG"
    assert "行号15" in result.suggestions
    assert "频率/频次列为空" in result.suggestions


def test_sparse_special_characteristic_row_requires_control_method(tmp_path):
    workbook = control_plan()
    for address in ("E15", "F15", "K15", "L15"):
        workbook.active[address] = None

    result = check_rule(tmp_path, workbook, 5)

    assert result.status == "NG"
    assert "行号15" in result.suggestions
    assert "控制方法列为空" in result.suggestions


def test_cross_file_rule_without_group_context_does_not_pass(tmp_path):
    result = check_rule(tmp_path, Workbook(), 3)

    assert result.status == "NG"
    assert "同次提交组上下文" in result.suggestions


def test_readable_plan_without_stage_information_is_ng_not_a_system_error(tmp_path):
    result = check_rule(tmp_path, control_plan(), 1)

    assert result.status == "NG"
    assert "适用阶段" in result.suggestions


@pytest.mark.parametrize("prefix", [True, False], ids=["prefix", "suffix"])
@pytest.mark.parametrize("chosen", [0, 1, 2, None], ids=["prototype", "pre-launch", "production", "none"])
@pytest.mark.parametrize("empty", ["□", "☐", "▢"])
def test_stage_checkbox_stays_with_its_label(tmp_path, prefix, chosen, empty):
    stages = ["样件", "试生产", "批量生产"]
    boxes = ["■" if index == chosen else empty for index in range(3)]
    workbook = Workbook()
    workbook.active["A1"] = " ".join(box + label if prefix else label + box for label, box in zip(stages, boxes))

    result = check_rule(tmp_path, workbook, 1)

    assert result.status == ("PASS" if chosen == 2 else "NG")
    selected = [] if chosen is None else [["prototype", "pre_launch", "production"][chosen]]
    assert ControlPlanAgent._extract_selected_stages_from_cell(" ".join(box + label if prefix else label + box for label, box in zip(stages, boxes))) == selected


def test_unreadable_plan_is_still_a_system_error(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not an Excel archive")
    agent = ControlPlanAgent(
        AuditInput(audit_id="broken-plan", file_url=str(path)),
        RuleInput(rule_id="confirmed-rules", rule_profile="v3", type="控制计划", rules=[{"id": "1", "category": "local_excel_check", "rule_to_check": "文件类型勾选审核"}]),
    )
    try:
        result = agent.check(0)
    finally:
        agent.close()

    assert result is not None
    assert result.status == "ERR"


def test_v3_special_characteristic_can_use_genuinely_merged_product_evidence(tmp_path):
    workbook = control_plan()
    worksheet = workbook.active
    worksheet["G16"] = "SC"
    result_unmerged = check_rule(tmp_path, workbook, 4)
    assert result_unmerged.status == "NG"
    workbook = control_plan()
    worksheet = workbook.active
    worksheet["G16"] = "SC"
    worksheet.merge_cells("E15:E16")
    result_merged = check_rule(tmp_path, workbook, 4)
    assert result_merged.status == "PASS"


@pytest.mark.parametrize("rule_number", [4, 5])
def test_special_characteristic_checks_cover_later_table_sheets(tmp_path, rule_number):
    workbook = control_plan()
    later = workbook.copy_worksheet(workbook.active)
    later.title = "控制计划第2页"
    if rule_number == 4:
        later["E15"] = later["F15"] = None
    else:
        later["L15"] = None
    result = check_rule(tmp_path, workbook, rule_number)
    assert result.status == "NG"


def test_pdf_stage_words_without_actual_mark_cannot_pass(tmp_path):
    workbook = control_plan()
    workbook.active["A1"] = "样件 试生产 批量生产"
    result = check_rule(tmp_path, workbook, 1, source_pdf_url="original.pdf")
    assert result.status == "UNABLE"


@pytest.mark.parametrize("rule_number,cell", [(4, "G15"), (4, "E15"), (5, "G15"), (5, "L15")])
def test_selected_unknown_cell_cannot_pass_special_characteristic_rules(tmp_path, rule_number, cell):
    workbook = control_plan()
    workbook.active[cell] = "#N/A"
    result = check_rule(tmp_path, workbook, rule_number)
    assert result.status == "UNABLE"


def test_known_process_evidence_is_sufficient_when_product_cell_is_unknown(tmp_path):
    workbook = control_plan()
    workbook.active["E15"] = "#N/A"
    workbook.active["F15"] = "known process"
    result = check_rule(tmp_path, workbook, 4)
    assert result.status == "PASS"


@pytest.mark.parametrize("rule_number", [1, 4, 5])
def test_control_plan_counts_real_stage_or_detail_predicates(tmp_path, rule_number):
    workbook = control_plan()
    workbook.active["A1"] = "☒ 生产"
    result = check_rule(tmp_path, workbook, rule_number)
    assert result.status == "PASS"
    assert result.checks_performed > 0


@pytest.mark.parametrize("rule_number", [4, 5])
def test_control_plan_empty_detail_region_does_not_invent_a_check(tmp_path, rule_number):
    workbook = control_plan()
    workbook.active.delete_rows(14, 2)
    result = check_rule(tmp_path, workbook, rule_number)
    assert result.checks_performed == 0
