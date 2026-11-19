"""U05 findings cite the MSA plan cells their basis names; the verdict and the basis stay as they were."""

import json

from audit.checker_lib.uaes.msa import UaesMsaAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _check(tmp_path, rows, rule):
    book = Workbook()
    book.active.title = "MSA计划"
    book.active.append(["序号", "量具名称", "量具编号", "计划日期", "备注"])
    for row in rows:
        book.active.append(row)
    path = tmp_path / "msa.xlsx"
    book.save(path)
    body = preset_rule_body("UAES", "U05")
    agent = UaesMsaAgent(AuditInput(audit_id="msa", file_url=str(path)), RuleInput(rule_id="u05", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check([name for name, _ in agent.declared_rules()].index(rule))


def _cited(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def test_an_empty_plan_field_cites_its_cell(tmp_path):
    result = _check(tmp_path, [[1, "卡尺", "G-01", "2026-01", None], [2, "塞规", None, "2026-02", None]], "rule_1")
    assert result.status == "NG"
    assert result.suggestions == "MSA计划“MSA计划”第3行量具编号为空。"
    assert _cited(result) == ["C3"]


def test_a_complete_plan_cites_nothing(tmp_path):
    result = _check(tmp_path, [[1, "卡尺", "G-01", "2026-01", None]], "rule_1")
    assert result.status == "PASS" and _cited(result) == []
