"""U07 findings cite the PFMEA cells their basis names; the verdict and the basis stay as they were."""

import json

from audit.checker_lib.uaes.fmea import UaesPfmeaAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook
from openpyxl.utils.cell import get_column_letter

HEADER = ["工序/功能/要求", "潜在失效模式", "潜在失效后果", "严重度", "潜在失效起因/机理", "频度", "探测度"]


def _check(tmp_path, rows, rule):
    path = tmp_path / "pfmea.xlsx"
    book = Workbook()
    book.active.title = "PFMEA"
    for row in rows:
        book.active.append(row)
    book.save(path)
    body = preset_rule_body("UAES", "U07")
    agent = UaesPfmeaAgent(AuditInput(audit_id="pfmea", file_url=str(path)), RuleInput(rule_id="u07", **{key: value for key, value in body.items() if key != "kind"}))
    return agent.check([name for name, _ in agent.declared_rules()].index(rule))


def _sources(result):
    return [json.loads(source.locator)["cell"] for source in (result.evidence.sources if result.evidence else ())]


def _spy(monkeypatch):
    """The cells a basis cites; the agent already records every cell it reads, so the cited ones are spied on."""
    cited = []
    original = UaesPfmeaAgent.cite

    def cite(self, sheet, row, column):
        cited.append(f"{get_column_letter(column)}{row}")
        return original(self, sheet, row, column)

    monkeypatch.setattr(UaesPfmeaAgent, "cite", cite)
    return cited


def test_a_blank_score_cites_that_score_cell(tmp_path, monkeypatch):
    cited = _spy(monkeypatch)
    rows = [HEADER, ["10 铣面", "平面度超差", "装配困难", "7", "刀具磨损", "3", "4"], ["20 钻孔", "孔径超差", "装配困难", None, "钻头磨损", "3", "4"]]
    result = _check(tmp_path, rows, "rule_2")
    assert result.status == "NG"
    assert result.suggestions == "PFMEA“PFMEA”第3行S（严重度）为空。"
    assert cited == ["D3"] and "D3" in _sources(result)


def test_a_safety_effect_scored_low_cites_its_severity(tmp_path, monkeypatch):
    cited = _spy(monkeypatch)
    rows = [HEADER, ["10 铣面", "平面度超差", "涉及安全，制动失效", "7", "刀具磨损", "3", "4"]]
    result = _check(tmp_path, rows, "rule_2")
    assert result.status == "NG" and result.suggestions.startswith("PFMEA“PFMEA”第2行失效后果")
    assert cited == ["D2"] and "D2" in _sources(result)


def test_a_lessons_learned_citation_cites_its_cell(tmp_path, monkeypatch):
    cited = _spy(monkeypatch)
    rows = [["经验教训：引用F01R10Y776售后8D报告"], HEADER, ["10 铣面", "平面度超差", "装配困难", "7", "刀具磨损", "3", "4"]]
    result = _check(tmp_path, rows, "rule_1")
    assert result.status == "PASS" and "第1行引用经验教训" in result.suggestions
    assert cited == ["A1"] and "A1" in _sources(result)
