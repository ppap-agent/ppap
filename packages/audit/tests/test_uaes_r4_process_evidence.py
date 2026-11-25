"""R4 scope and evidence survive new process names, column positions, suppliers and diagram page breaks."""

from types import SimpleNamespace

import fitz
import pytest
from audit.checker_lib.uaes.v1.fmea_r4 import UaesPfmeaRevision4, action_failure_witness
from audit.checker_lib.uaes.v1.process_flow_r4 import UaesProcessFlowRevision4
from audit.contracts import AuditInput, RuleInput
from audit.engine.rule_engine import Sheet
from audit.presets import preset_rule_body
from openpyxl import Workbook


@pytest.mark.parametrize(
    ("pfd", "actions", "modes", "quoted"),
    [
        ("丝网印刷", "表面预处理、丝网印刷、固化", "固化：硬度不足；丝网印刷：图案偏位", "丝网印刷：图案偏位"),
        ("Crimp 压接", "下料，压接", "压接:端子未压紧", "压接:端子未压紧"),
    ],
)
def test_aggregated_actions_have_source_attributed_failures(pfd, actions, modes, quoted):
    assert quoted in action_failure_witness(pfd, actions, modes)


def test_separate_operation_name_column_binds_row_without_a_process_dictionary():
    book = Workbook()
    ws = book.active
    ws.title = "PFMEA"
    ws.append(["严重度", "频度", "潜在失效模式", "探测度", "工序名称", "潜在失效原因"])
    ws.append([4, 2, "覆层局部脱落", 3, "等离子喷涂", "作用距离过大"])
    reader = UaesPfmeaRevision4(AuditInput(audit_id="fmea", file_url="fixture.xlsx"), RuleInput.of(preset_rule_body("UAES", "U07"), rule_id="u07"))
    item = SimpleNamespace(sheet=Sheet(reader.rule_ctx(book), ws), row=2, values={"功能": "提供耐磨覆层", "失效模式": "覆层局部脱落"})
    assert reader.process_witness("等离子喷涂", "", item) == "PFMEA工序名称“等离子喷涂”，同行失效模式“覆层局部脱落”"


@pytest.mark.parametrize(("row", "col"), [(1, 1), (5, 4)])
def test_part_identity_comes_from_its_own_merged_label_and_case_is_preserved(row, col):
    book = Workbook()
    ws = book.active
    ws.cell(row, col, "模具号")
    ws.cell(row, col + 1, "F012345678")
    ws.merge_cells(start_row=row + 1, end_row=row + 1, start_column=col, end_column=col + 1)
    ws.cell(row + 1, col, "Part No/零件号")
    ws.cell(row + 1, col + 2, "vB-104.7/a")
    audit = UaesPfmeaRevision4(AuditInput(audit_id="fmea", file_url="example.xlsx"), RuleInput.of(preset_rule_body("UAES", "U07"), rule_id="u07"))
    sheet = Sheet(audit.rule_ctx(book), ws)
    assert audit._header_parts(sheet, row + 2) == {"vB-104.7/a"}


def test_horizontal_identity_fields_use_the_value_below_the_correct_heading():
    book = Workbook()
    ws = book.active
    ws.append(["零件号", "图号"])
    ws.append(["R-82b", "R-82a"])
    reader = UaesPfmeaRevision4(AuditInput(audit_id="fmea", file_url="fixture.xlsx"), RuleInput.of(preset_rule_body("UAES", "U07"), rule_id="u07"))
    assert reader._header_parts(Sheet(reader.rule_ctx(book), ws), 2) == {"R-82b"}


def test_rendered_branch_evidence_can_come_from_second_printed_page(tmp_path, monkeypatch):
    from audit.checker_lib.uaes.v1 import process_flow_r4 as flow
    from documents.excel import visual

    path = tmp_path / "flow.xlsx"
    path.touch()
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        pdf = document.tobytes()
    reader = UaesProcessFlowRevision4(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(preset_rule_body("UAES", "U16"), rule_id="u16"))
    monkeypatch.setattr(flow.Table, "find_sheets", lambda *a, **kw: [SimpleNamespace(title="流程图")])
    monkeypatch.setattr(visual, "has_region_graphics", lambda *a, **kw: True)
    monkeypatch.setattr(visual, "render_xlsx_pdf", lambda *a, **kw: pdf)
    pages = []

    def read(image, title, number):
        pages.append((title, number))
        return "第二页原图的来源" if number == 2 else None

    monkeypatch.setattr(reader, "read_disposition", read)
    assert reader.visual_node_evidence(None, ["异常处理"]) == {"异常处理": "第二页原图的来源"}
    assert pages == [("流程图", 1), ("流程图", 2)]
