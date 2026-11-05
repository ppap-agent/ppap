"""Original-source regressions from the reviewed V2 corpus differences."""

import fitz
import pytest
from audit.checkers.v2.dpvr_excel_agent import DpvrExcelAgent
from audit.contracts import AuditInput, RuleInput
from openpyxl import Workbook


def rules(agent_type, count):
    return RuleInput(
        rule_id="reviewed-v2-rules",
        rule_profile="v2",
        type=agent_type.DOC_TYPE.get_type_name(),
        rules=[{"id": str(number), "category": "原件检查", "rule_to_check": f"规则{number}"} for number in range(1, count + 1)],
    )


@pytest.mark.parametrize(("conclusion", "quantity", "rule_number", "expected"), [("P", "1pc", 2, "PASS"), ("F", "1pc", 2, "NG"), ("P", "1pc", 6, "PASS"), ("P", "", 6, "NG")])
def test_paragraph_conversion_uses_bound_native_fields_not_footer_text(tmp_path, conclusion, quantity, rule_number, expected):
    source = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page()
        for y, label, english, value in ((100, "判定结果", "Conclusion", conclusion), (160, "测试样品数量", "Number of Test Sample", quantity)):
            page.insert_text((60, y), label, fontname="china-s", fontsize=12)
            page.insert_text((60, y + 16), english, fontsize=10)
            page.insert_text((270, y + 4), value, fontsize=12)
        document.save(source)
    converted = tmp_path / "converted.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "DPVR报告"
    sheet.append(["试验结论\n判定结果\nConclusion\n原页段落\n其余说明"])
    sheet.append(["检测地址1：某地址"])
    workbook.save(converted)
    workbook.close()
    agent = DpvrExcelAgent(
        AuditInput(audit_id="native-fields", file_url=str(converted), file_metadata={"source_pdf_url": str(source)}),
        rules(DpvrExcelAgent, 6),
    )
    try:
        assert agent.check(rule_number - 1).status == expected
    finally:
        agent.close()
