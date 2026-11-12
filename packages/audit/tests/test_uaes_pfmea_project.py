"""U07:3 pairs 同项目、同物料编号: a list that names no project anywhere cannot be paired, which fails the clause."""

import fitz
import pytest
from audit.checker_lib.uaes.fmea import UaesPfmeaAgent
from audit.contracts import AuditInput
from openpyxl import Workbook


@pytest.mark.parametrize(("text", "expected"), [("产品件号 Part No. F01R20V040", True), ("项目 Project: V040", False)])
def test_a_pdf_list_states_no_project_only_when_its_whole_text_layer_has_none(tmp_path, text, expected):
    path = tmp_path / "list.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((40, 40), text, fontname="china-s")
        document.save(path)
    assert UaesPfmeaAgent.states_no_project(AuditInput(audit_id="list", file_url=str(path))) is expected


@pytest.mark.parametrize(("label", "expected"), [("零件号", True), ("UAES项目编号", False)])
def test_a_workbook_list_states_no_project_only_when_no_cell_names_one(tmp_path, label, expected):
    path = tmp_path / "list.xlsx"
    book = Workbook()
    book.active.append([label, "F01R20V040"])
    book.save(path)
    assert UaesPfmeaAgent.states_no_project(AuditInput(audit_id="list", file_url=str(path))) is expected
