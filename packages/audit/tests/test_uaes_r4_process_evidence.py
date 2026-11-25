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

    def read(image):
        pages.append(image)
        return ("“检测不合格” → “隔离”", [100, 100, 600, 400]) if len(pages) == 2 else None

    monkeypatch.setattr(reader, "read_disposition", read)
    assert reader.visual_node_evidence(None, ["异常处理"]) == {"异常处理": "异常处理（流程图第2页原图局部）“检测不合格” → “隔离”"}
    assert len(pages) == 2


def flow_pdf(path, rotation=0):
    with fitz.open() as document:
        page = document.new_page(width=600, height=400)
        page.insert_text((40, 60), "1 原材料检验")
        page.set_rotation(rotation)
        document.save(path)
    return path


@pytest.mark.parametrize("found", [None, "unconfirmed"])
def test_pdf_chart_read_page_by_page_is_complete_only_without_an_unconfirmed_branch(tmp_path, monkeypatch, found):
    from audit.checker_lib.uaes.v1 import process_flow_r4 as flow

    path = flow_pdf(tmp_path / "flow.pdf")
    reader = UaesProcessFlowRevision4(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(preset_rule_body("UAES", "U16"), rule_id="u16"))
    monkeypatch.setattr(reader, "read_disposition", lambda image: flow._UNCONFIRMED if found else None)
    assert reader.visual_node_evidence(None, ["异常处理"]) == {}
    reading = reader.__dict__["_node_reading"]
    assert reading["complete"] is (found is None), reading
    if found is None:
        assert reading["scope"] == "已读完原件全部1页的工序与文字，并逐页看过原图（共1页）", reading
        (page,) = [s.locator for s in reader.legacy()._evidence_sources]
        assert '"kind":"pdf_region"' in page and '"page":1' in page


@pytest.mark.parametrize(
    ("located", "local", "expected"),
    [
        (None, None, None),
        ([100, 100, 600, 400], {"condition": "检测不合格", "destination": "隔离", "arrow": True}, ("“检测不合格” → “隔离”", [100, 100, 600, 400])),
        # The arrow it pointed at is an ordinary process step: no failed branch there.
        ([5, 120, 752, 845], {"condition": "加工", "destination": "熔化", "arrow": True}, None),
        # A failed branch going somewhere not confirmed as a disposition, or an unreadable condition, stays open.
        ([100, 100, 600, 400], {"condition": "不合格", "destination": "返工区", "arrow": True}, "unconfirmed"),
        ([100, 100, 600, 400], {"condition": "", "destination": "", "arrow": False}, "unconfirmed"),
        ([0, 0, 1000, 1000], None, "unconfirmed"),
    ],
)
def test_located_branch_is_settled_by_its_local_reading(monkeypatch, located, local, expected):
    import io

    from audit.checker_lib.uaes.v1 import process_flow_r4 as flow
    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(image, format="PNG")
    reader = UaesProcessFlowRevision4(AuditInput(audit_id="flow", file_url="flow.pdf"), RuleInput.of(preset_rule_body("UAES", "U16"), rule_id="u16"))
    answers = iter([{"box": located}, local])
    monkeypatch.setattr(reader, "transcribe", lambda *a, **kw: next(answers))
    found = reader.read_disposition(image.getvalue())
    assert (found is flow._UNCONFIRMED) if expected == "unconfirmed" else found == expected


def test_pdf_chart_never_completes_another_missing_node_class(tmp_path, monkeypatch):
    path = flow_pdf(tmp_path / "flow.pdf")
    reader = UaesProcessFlowRevision4(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(preset_rule_body("UAES", "U16"), rule_id="u16"))
    monkeypatch.setattr(reader, "read_disposition", lambda image: None)
    assert reader.visual_node_evidence(None, ["来料控制", "异常处理"]) == {}
    assert reader.__dict__["_node_reading"]["complete"] is False


def test_pdf_branch_on_a_rotated_page_is_located_in_unrotated_page_points(tmp_path, monkeypatch):
    import json

    path = flow_pdf(tmp_path / "flow.pdf", rotation=90)
    reader = UaesProcessFlowRevision4(AuditInput(audit_id="flow", file_url=str(path)), RuleInput.of(preset_rule_body("UAES", "U16"), rule_id="u16"))
    # The upper left quarter of the page as displayed (rotated to 400 x 600).
    monkeypatch.setattr(reader, "read_disposition", lambda image: ("“不合格” → “返工”", [0, 0, 500, 500]))
    assert reader.visual_node_evidence(None, ["异常处理"]) == {"异常处理": "异常处理（第1页原图局部）“不合格” → “返工”"}
    (source,) = reader.legacy()._evidence_sources
    locator = json.loads(source.locator)
    assert (locator["kind"], locator["coordinate_system"], locator["bbox"]) == ("pdf_region", "pdf_points_unrotated", [0.0, 200.0, 300.0, 400.0])
