"""Actual Chinese PDF grid extraction, with no synthetic conversion artifact."""

import fitz
import pytest
from audit.checkers.v4.li_auto import PDF_AGENTS
from audit.contracts import AuditInput, RuleInput
from audit.engine.runtime import bind_runtime
from audit.presets import preset_rule_body
from documents.conversion import convert_pdf_to_excel_auto


class NativePdfRuntime:
    def convert_pdf_to_excel(self, file_ref, **kwargs):
        return convert_pdf_to_excel_auto(file_ref, allow_ocr=False, **kwargs)


@pytest.mark.parametrize(("verdict", "expected"), [("通过", "PASS"), ("基本合格", "NG")])
def test_real_untamplated_pdf_preserves_exact_verdict_and_page_evidence(tmp_path, verdict, expected):
    path = tmp_path / "真实验证报告.pdf"
    document = fitz.open()
    page = document.new_page(width=500, height=600)
    for index, (label, value) in enumerate([("报告编号", "R-001"), ("试验结论", verdict), ("试验类型", "PV"), ("样品数量", "3")]):
        page.insert_text((60, 90 + 40 * index), label, fontname="china-s", fontsize=12)
        page.insert_text((260, 90 + 40 * index), value, fontname="china-s", fontsize=12)
    for y in range(60, 221, 40):
        page.draw_line((50, y), (450, y))
    for x in (50, 250, 450):
        page.draw_line((x, 60), (x, 220))
    document.save(path)
    document.close()
    with bind_runtime(NativePdfRuntime()):
        agent = PDF_AGENTS["L03"](AuditInput(audit_id="pdf-version", file_url=str(path)), RuleInput(rule_id="r", **preset_rule_body("理想", "L03")))
        try:
            assert agent._execute_rule_check(1).status == "PASS"
            result = agent._execute_rule_check(2)
            assert result.status == expected
            assert result.evidence.source_format == "pdf"
            assert any("page:1" in source.locator for source in result.evidence.sources)
            assert agent._execute_rule_check(3).status == "PASS"
            assert agent._execute_rule_check(6).status == "PASS"
            assert agent._execute_rule_check(5).status == "UNABLE"
        finally:
            agent.close()
