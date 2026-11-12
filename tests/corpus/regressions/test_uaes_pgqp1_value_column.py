"""U06:4 on the 776 capability reports: PGQP1's "SC/特殊特性" column holds characteristic values, not marks."""

from pathlib import Path

import pytest
from audit.presets import preset_rule_body


@pytest.mark.parametrize("report", ["776-机加CPK.xlsx", "776-毛坯CPK.xlsx"])
def test_unmarked_special_characteristics_fail_as_unidentifiable(request, report):
    if request.config.getoption("--ftype", default=None) not in (None, "excel"):
        pytest.skip("Original workbook regression")
    from audit.checker_lib.uaes.engineering import UaesCapabilityAgent
    from audit.contracts import AuditInput, GroupContextInput, RuleInput

    root = next((Path(__file__).parents[3] / "assets/uaes/samples").glob("F01R10Y776*"))
    cpk = AuditInput(audit_id="cpk", file_url=str(next(root.glob(f"测试文件/07 过程能力研究/{report}"))))
    plan = AuditInput(audit_id="pgqp1", file_url=str(next(root.glob("比对文件/采购件质量计划/*776.xlsx"))))
    group = GroupContextInput(by_audit_id={"cpk": cpk, "pgqp1": plan}, by_doc_type={"E12": [plan]})
    body = preset_rule_body("UAES", "U06")
    agent = UaesCapabilityAgent(cpk, RuleInput(rule_id="u06", **{key: value for key, value in body.items() if key != "kind"}), group)
    result = agent.check([name for name, _ in agent.declared_rules()].index("rule_4"))
    assert result.status == "NG" and "无法辨识" in result.suggestions
