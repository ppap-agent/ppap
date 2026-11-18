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


def test_an_unidentifiable_part_number_fails_the_pgqp1_clause_and_asks_for_a_person(tmp_path, monkeypatch):
    # U07:4 pairs 同零件号 and reads "无法辨识则不通过": an unlabelled part number is not guessed from a nearby cell.
    from audit.contracts import RuleInput
    from audit.presets import preset_rule_body

    path = tmp_path / "pfmea.xlsx"
    book = Workbook()
    book.active.append(["", "F01R10Y776"])
    book.save(path)
    monkeypatch.setattr(UaesPfmeaAgent, "require_readable", lambda self, kinds: None)
    monkeypatch.setattr(UaesPfmeaAgent, "batch_files", lambda self, kind: [object()])
    body = preset_rule_body("UAES", "U07")
    agent = UaesPfmeaAgent(AuditInput(audit_id="pfmea", file_url=str(path)), RuleInput(rule_id="u07", **{key: value for key, value in body.items() if key != "kind"}))
    monkeypatch.setattr(UaesPfmeaAgent, "sheets", lambda self, ctx: [])
    names = [name for name, _ in agent.declared_rules()]
    result = agent.check(names.index("rule_4"))
    assert result.status == "NG"
    assert "未标注零件号" in result.suggestions and "请人工复核" in result.suggestions
