"""U01 findings cite the report cells their basis names; the verdict and the basis stay as they were."""

import json

from audit.checker_lib.uaes.inspection import UaesInspectionAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook

HEADER = ["No.", "Requirements/要求", "Actual-Value Supplier/供应商实测值", "Evaluation"]


def _check(tmp_path, rows, rule):
    book = Workbook()
    book.remove(book.active)
    for title in ("尺寸报告", "材料报告", "功能报告", "可靠性报告"):
        sheet = book.create_sheet(title)
        sheet.append(HEADER)
        for row in rows if title == "尺寸报告" else [[1, None, None, None]]:
            sheet.append(row)
    path = tmp_path / "inspection.xlsx"
    book.save(path)
    body = preset_rule_body("UAES", "U01")
    agent = UaesInspectionAgent(AuditInput(audit_id="inspection", file_url=str(path)), RuleInput.of(body, rule_id="u01"))
    return agent.check([name for name, _ in agent.declared_rules()].index(rule))


def _cited(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def test_a_reading_out_of_range_and_a_rejected_row_cite_their_cells(tmp_path):
    result = _check(tmp_path, [[1, "10±0.1", 10.5, "OK"], [2, "5±0.1", 5.0, "NOT OK"]], "rule_2")
    assert result.status == "NG"
    assert result.suggestions == "“尺寸报告”第2行要求“10±0.1”，实测值10.5超出要求范围；“尺寸报告”第3行结果不是OK。"
    assert _cited(result) == ["C2", "D3"]


def test_a_pass_cites_only_the_rows_it_names(tmp_path):
    result = _check(tmp_path, [[1, "10±0.1", 10.09, "OK"], [2, "5±0.1", 5.0, "OK"]], "rule_2")
    assert result.status == "PASS"
    assert "“尺寸报告”第2行“10±0.1”" in result.suggestions and "第3行" not in result.suggestions
    assert _cited(result) == ["B2"]
