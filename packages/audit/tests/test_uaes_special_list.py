"""U12:2 "缺列则不通过": a readable list without the four columns, or with no characteristic under them, fails."""

import pytest
from audit.checker_lib.uaes.special_characteristics import UaesSpecialCharacteristicsAgent
from audit.contracts import AuditInput, RuleInput
from audit.presets import preset_rule_body
from openpyxl import Workbook


@pytest.mark.parametrize(
    ("title", "rows", "said"),
    [("Sheet", [["产品名称", "磁性罩"]], "缺列不通过"), ("特殊特性清单", [["序号", "特殊特性名称", "特性值", "特性类别"]], "没有特性行")],
)
def test_a_readable_list_without_the_columns_or_rows_fails(tmp_path, title, rows, said):
    path = tmp_path / "list.xlsx"
    book = Workbook()
    book.active.title = title
    for row in rows:
        book.active.append(row)
    book.save(path)
    body = preset_rule_body("UAES", "U12")
    agent = UaesSpecialCharacteristicsAgent(AuditInput(audit_id="u12", file_url=str(path)), RuleInput.of(body, rule_id="u12"))
    result = agent.check([name for name, _ in agent.declared_rules()].index("rule_2"))
    assert result.status == "NG" and said in result.suggestions
