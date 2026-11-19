"""Human-confirmation export retains the original machine result and source, without internal identifiers."""

import re

import pymupdf
from documents.fact_confirmation_pdf import render_fact_confirmation_pdf

ROUND = "7d1c2b9e-3f4a-4c5d-8e6f-0a1b2c3d4e5f"
VERSION = "0f9e8d7c-6b5a-4938-a271-605f4e3d2c1b"
USER = "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def test_human_confirmation_report_preserves_machine_result_without_internal_ids():
    answer = {"file_version_id": VERSION, "field": "visual.sop", "prompt": "SOP 栏是否勾选？", "value": True, "answered_by": USER, "answered_at": "2026-11-07T01:00:00Z"}
    answer["locator"] = {"kind": "pdf_page", "page": 1}
    source = {
        "round_id": ROUND,
        "revision": 1,
        "originals": {VERSION: "original.pdf"},
        "answerers": {USER: "Alice"},
        "answers": [{**answer, "operation_id": "9b8a7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d", "round_id": ROUND, "result_attempt": 1}],
        "machine_findings": [{"item_key": f"{VERSION}:R1", "status": "UNABLE", "suggestions": "Unknown checkbox", "locator": {"file_version_id": VERSION, "rule_item_id": "R1"}}],
        "results": [{"item_key": f"{VERSION}:R1", "status": "PASS", "suggestions": "Confirmed original", "sources": [answer]}],
    }
    with pymupdf.open(stream=render_fact_confirmation_pdf(source), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        for expected in ("原机器判定", "UNABLE", "人工确认", "PASS", "Alice", "original.pdf", "R1", "第 1 页", "SOP 栏是否勾选？"):
            assert expected in text
        assert not UUID.search(text)
        assert not UUID.search(repr(document.get_toc()))
        assert not UUID.search(repr(document.metadata))
        assert {key: value for key, value in document.metadata.items() if value and key != "format"} == {"title": "PPAP 原件事实人工确认报告", "subject": "PPAP 人工确认报告"}
        assert document.get_xml_metadata() == ""
