"""Native PDF continuation pages reach the XLSX reader with original provenance."""

import hashlib
import json

import fitz
import pytest
from audit.checkers.v4.uaes.control_plan import UaesControlPlanAgent
from audit.contracts import AuditInput, RuleInput


@pytest.mark.parametrize("last_resource,expected", [("设备2", "PASS"), ("", "NG"), (None, "UNABLE")])
def test_uaes_reads_all_native_pdf_continuation_pages_and_keeps_original_unknowns(tmp_path, last_resource, expected):
    path = tmp_path / "control-plan.pdf"
    with fitz.open() as document:
        for index, resource in enumerate(("设备1", last_resource)):
            page = document.new_page(width=400, height=220)
            for top in (20, 60, 100, 160):
                page.draw_line((20, top), (380, top))
            for left in (20, 380):
                page.draw_line((left, 20), (left, 160))
            page.draw_line((200, 60), (200, 160))
            for x, y, text in ((40, 45, "Control Plan"), (40, 85, "工序名称"), (220, 85, "生产资源"), (40, 130, f"工序{index + 1}"), (220, 130, resource or "")):
                if text:
                    page.insert_text((x, y), text, fontname="china-s", fontsize=11)
            if resource is None:
                page.draw_circle((260, 130), 10)
        document.save(path)
    original = path.read_bytes()
    agent = UaesControlPlanAgent(AuditInput(audit_id="frozen-pdf-version", file_url=str(path)), RuleInput(rule_id="uaes-cp", rule_profile="v4", preset_key="U08"))
    result = agent.check(0)
    assert result.status == expected
    assert path.read_bytes() == original
    assert result.evidence.source_format == "pdf"
    assert result.evidence.prepared_format == "xlsx"
    assert agent.native().has_unread_content() is (last_resource is None)
    sources = [json.loads(source.locator) for source in result.evidence.sources]
    assert {source["page"] for source in sources} == {1, 2}
    assert all(source["kind"] == "pdf_cell" and source["file_version_id"] == "frozen-pdf-version" and source["source_sha256"] == hashlib.sha256(original).hexdigest() for source in sources)
    assert all(source["bbox"] and source["prepared_sheet"] and source["prepared_cell"] and source["prepared_sha256"] for source in sources)
