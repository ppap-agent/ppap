"""Positive source-question and frozen-observation recovery journeys."""

from copy import deepcopy

import fitz
from audit.checkers.v3.excel_agent import rule
from audit.checkers.v4.uaes.native import UaesSourceAgent
from audit.contracts import AuditInput, RuleInput
from audit.fact_questions import BooleanFactEvaluation
from openpyxl import Workbook


class SourceQuestions(UaesSourceAgent):
    @rule("原件勾选")
    def rule_1(self):
        return self.visual_result({"sop_checked": "SOP勾选框已选中", "signed": "原件已签字"}, "SOP勾选及签字")

    @rule("原件编号")
    def rule_2(self):
        self.native().field(("零件号",))
        return "PASS", "已读取原件编号"


def test_located_checkbox_question_preserves_machine_and_recomputes_frozen_facts(tmp_path, monkeypatch):
    path = tmp_path / "source.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((50, 50), "SOP [ ] Signature: Alice")
        document.save(path)
    calls = []

    def observe(*args, **kwargs):
        calls.append("observe")
        return {"sop_checked": None, "signed": True}

    monkeypatch.setattr("audit.checkers.v4.visual_facts.read_original_pdf_visual_facts", observe)
    checker = SourceQuestions(AuditInput(audit_id="source-version", file_url=str(path)), RuleInput(rule_id="rule"))
    original = checker.check(0)
    before = deepcopy(original.model_dump(mode="json"))
    question = original.fact_questions[0]
    assert original.status == "UNABLE"
    assert question.field == "visual.sop_checked"
    assert question.locator == {"kind": "pdf_page", "page": 1}
    assert question.file_version_id == "source-version"
    recovered = BooleanFactEvaluation.model_validate(original.fact_evaluation.model_dump())
    assert recovered.evaluate({question.field: True})[0] == "PASS"
    assert original.model_dump(mode="json") == before
    assert calls == ["observe"]


def test_native_field_question_points_to_original_value_cell(tmp_path):
    path = tmp_path / "source.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "原件"
    sheet.append(["零件号", "=unresolved()"])
    book.save(path)
    book.close()
    checker = SourceQuestions(AuditInput(audit_id="source-version", file_url=str(path)), RuleInput(rule_id="rule"))
    result = checker.check(1)
    assert result.status == "UNABLE"
    assert result.fact_questions[0].answer_type == "text"
    assert result.fact_questions[0].locator == {"kind": "xlsx_cell", "sheet": "原件", "cell": "B1"}
    assert "零件号" in result.fact_questions[0].prompt
    assert result.fact_evaluation is None
