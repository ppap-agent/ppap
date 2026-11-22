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
    agent = UaesPfmeaAgent(AuditInput(audit_id="pfmea", file_url=str(path)), RuleInput.of(body, rule_id="u07"))
    monkeypatch.setattr(UaesPfmeaAgent, "sheets", lambda self, ctx: [])
    names = [name for name, _ in agent.declared_rules()]
    result = agent.check(names.index("rule_4"))
    assert result.status == "NG"
    assert "未加标签的编号" in result.suggestions and "请人工复核" in result.suggestions


@pytest.mark.parametrize(
    ("text", "cited"),
    [("经验教训：参考F01R10Y776 8D报告", True), ("Lessons learned: 2023 customer complaint #A12", True), ("经验教训 Lessons Learned", False)],
)
def test_the_clause_word_itself_cites_lessons_learned_when_it_names_a_source(text, cited):
    assert (UaesPfmeaAgent.lesson(text) is not None) is cited


@pytest.mark.parametrize(("header", "expected"), [("", "NG"), ("经验教训：引用F01R10Y776售后8D报告", "PASS")])
def test_a_failure_effect_is_not_a_lessons_learned_citation(tmp_path, header, expected):
    # U07:1 looks in 表头/附录/变更履历/修订说明; "客户抱怨" written as a failure effect is analysis, not input.
    from audit.contracts import RuleInput
    from audit.presets import preset_rule_body

    path = tmp_path / "pfmea.xlsx"
    book = Workbook()
    book.active.title = "PFMEA"
    book.active.append([header])
    book.active.append(["工序/功能/要求", "潜在失效模式", "潜在失效后果", "严重度", "潜在失效起因/机理", "频度", "探测度"])
    book.active.append(["10 铣面", "平面度超差", "客户抱怨，装配困难导致整车返修", "7", "刀具磨损", "3", "4"])
    book.save(path)
    body = preset_rule_body("UAES", "U07")
    agent = UaesPfmeaAgent(AuditInput(audit_id="pfmea", file_url=str(path)), RuleInput.of(body, rule_id="u07"))
    result = agent.check([name for name, _ in agent.declared_rules()].index("rule_1"))
    assert result.status == expected, result.suggestions
