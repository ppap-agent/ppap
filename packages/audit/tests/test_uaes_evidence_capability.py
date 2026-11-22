"""U06 and U16 findings cite the cells their basis names; the verdict and the basis stay as they were."""

import json

from audit.checker_lib.uaes.engineering import UaesCapabilityAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _check(tmp_path, rows, rule):
    book = Workbook()
    book.active.title = "汇总"
    book.active.append(["工程名", "目标\n(CPK)", "结果\n(CPK)", "反应计划"])
    for row in rows:
        book.active.append(row)
    path = tmp_path / "capability.xlsx"
    book.save(path)
    body = preset_rule_body("UAES", "U06")
    agent = UaesCapabilityAgent(AuditInput(audit_id="capability", file_url=str(path)), RuleInput.of(body, rule_id="u06"))
    return agent.check([name for name, _ in agent.declared_rules()].index(rule))


def _cited(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def test_a_result_below_its_target_without_a_reaction_plan_cites_the_result(tmp_path):
    result = _check(tmp_path, [["1#孔径", 1.33, 1.67, None], ["2#孔径", 1.33, 0.9, None]], "rule_3")
    assert result.status == "NG"
    assert result.suggestions == "能力汇总“汇总”第3行结果CPK0.9低于目标CPK1.33，同行没有反应计划或备注。"
    assert _cited(result) == ["C3"]


def test_a_summary_error_value_cites_its_cell(tmp_path):
    result = _check(tmp_path, [["1#孔径", 1.33, "#DIV/0!", None]], "rule_3")
    assert result.status == "NG" and "第2行结果CPK为错误值" in result.suggestions
    assert _cited(result) == ["C2"]
