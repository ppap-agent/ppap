"""Revision 4: signatures and cited attachments are observations of the submitted originals."""

import json

import fitz
from audit.checker_lib.uaes.v1.engineering_approval import ROLES, SIGN_SCHEMA
from audit.checker_lib.uaes.v1.engineering_approval_r4 import ApprovalR4
from audit.checker_lib.uaes.v1.warrant import _Reading
from audit.checker_lib.uaes.v1.warrant_r4 import WarrantR4
from audit.contracts import AuditInput, GroupContextInput
from openpyxl import Workbook


def test_pdf_vector_signatures_are_read_from_the_cover_image(tmp_path, monkeypatch):
    path = tmp_path / "工程批准.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        for index, role in enumerate(ROLES):
            x = 30 + 174 * index
            page.insert_text((x, 100), role + "：", fontname="china-s", fontsize=12)
            page.insert_text((x + 56, 100), "2025-11-06", fontsize=11)
            page.draw_polyline([(x + 37, 100), (x + 41, 91), (x + 47, 99)])
        doc.save(path)
    agent = ApprovalR4.__new__(ApprovalR4)
    agent.audit_file = AuditInput(audit_id="cover", file_url=str(path))

    def read(image, prompt, schema, subject):
        assert schema is SIGN_SCHEMA and image.startswith(b"\x89PNG")
        return {role: {"signed": True, "date": "2025-11-06"} for role in ROLES}

    monkeypatch.setattr(agent, "transcribe", read)
    status, reason = agent.full_approval()
    assert status == "PASS" and "不适用" in reason
    assert any(json.loads(e.locator).get("fact") == "sign_off" and e.kind == "visual" for e in agent._cited)


def test_cited_warrant_appendix_uses_the_named_frozen_file_and_cell(tmp_path):
    path = tmp_path / "压装工艺附件.xlsx"
    book = Workbook()
    book.active.title = "提交说明"
    book.active["J29"] = "生产节拍：42秒"
    book.save(path)
    agent = WarrantR4.__new__(WarrantR4)
    agent.audit_file = AuditInput(audit_id="warrant", file_url=str(tmp_path / "保证书.xls"))
    annex = AuditInput(audit_id="attachment-version", file_url=str(path))
    agent.group_context = GroupContextInput(unavailable_files={"other-frozen-input": None})
    agent._transcription = {}
    reading = _Reading(agent)
    pointer = f"详见《{path.name}》提交说明!J29"
    agent.check_explanation(reading, pointer)
    assert reading.unknown and not reading.failed

    agent.group_context.by_audit_id[annex.audit_id] = annex
    reading = _Reading(agent)
    agent.check_explanation(reading, pointer)
    assert not reading.failed and not reading.unknown
    cited = json.loads(agent._cited[-1].locator)
    assert (cited["file_version_id"], cited["sheet"], cited["cell"]) == (annex.audit_id, "提交说明", "J29")
