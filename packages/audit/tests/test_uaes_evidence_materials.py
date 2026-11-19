"""U04 findings cite the material list cells their basis names; the verdict and the basis stay as they were."""

import json

from audit.checker_lib.uaes.materials import UaesMaterialsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


def _check(tmp_path, rows, rule):
    book = Workbook()
    sheet = book.active
    sheet.title = "XXX项目 材料及分供方清单"
    sheet.append(["UAES项目编号：\nUAES Project No.:", "P1", "零件名称:\nPart Name:", "壳体"])
    sheet.append(["IMDS 表单ID号：\nIMDS data sheet ID:", None, "零件号:\nPart No:", "F01R10Y776"])
    sheet.append(["序号", "零件可视图", "材料名称和材料牌号", "供应商"])
    for row in rows:
        sheet.append(row)
    path = tmp_path / "materials.xlsx"
    book.save(path)
    body = preset_rule_body("UAES", "U04")
    agent = UaesMaterialsAgent(AuditInput(audit_id="materials", file_url=str(path)), RuleInput(rule_id="u04", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check([name for name, _ in agent.declared_rules()].index(rule))


def _cited(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def test_empty_fields_cite_their_cells(tmp_path):
    result = _check(tmp_path, [[1, "图", "ALSI12", None]], "rule_1")
    assert result.status == "NG"
    assert result.suggestions == "材料及分供方清单除备注外有空字段：IMDS表单ID号（B2）为空；第4行供应商为空。"
    assert _cited(result) == ["B2", "D4"]
