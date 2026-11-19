"""U12 findings cite the list cells their basis names; the verdict and the basis stay as they were."""

import json

from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook
from openpyxl.utils.cell import get_column_letter


def _check(tmp_path, rows, rule):
    path = tmp_path / "list.xlsx"
    book = Workbook()
    book.active.title = "特殊特性清单"
    for row in rows:
        book.active.append(row)
    book.save(path)
    body = preset_rule_body("UAES", "U12")
    agent = UaesSpecialCharacteristicsAgent(AuditInput(audit_id="u12", file_url=str(path)), RuleInput(rule_id="u12", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check([name for name, _ in agent.declared_rules()].index(rule))


def _sources(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def _spy(monkeypatch):
    """The cells a basis cites; the agent already records every cell it reads, so the cited ones are spied on."""
    cited = []
    original = UaesSpecialCharacteristicsAgent.cite

    def cite(self, sheet, row, column):
        cited.append(f"{get_column_letter(column)}{row}")
        return original(self, sheet, row, column)

    monkeypatch.setattr(UaesSpecialCharacteristicsAgent, "cite", cite)
    return cited


def test_a_blank_required_cell_is_cited(tmp_path, monkeypatch):
    cited = _spy(monkeypatch)
    rows = [["序号", "特殊特性名称", "特性值", "特性类别"], ["1", "孔径", "Φ8±0.1", "SC"], ["2", "平面度", None, "SC"]]
    result = _check(tmp_path, rows, "rule_2")
    assert result.status == "NG"
    assert result.suggestions == "特殊特性清单“特殊特性清单”第3行（特性“平面度”）特性值为空。"
    assert cited == ["C3"] and "C3" in _sources(result)


def test_a_blank_part_cell_is_cited(tmp_path, monkeypatch):
    cited = _spy(monkeypatch)
    rows = [["序号", "产品名称/零件号", "特殊特性名称", "特性值", "特性类别"], ["1", "磁性罩 F01R10Y776", "孔径", "Φ8±0.1", "SC"], ["2", None, "平面度", "0.05", "SC"]]
    result = _check(tmp_path, rows, "rule_1")
    assert result.status == "NG"
    assert result.suggestions == "特殊特性清单“特殊特性清单”第3行产品名称/零件号为空。"
    assert cited == ["B3"] and "B3" in _sources(result)
