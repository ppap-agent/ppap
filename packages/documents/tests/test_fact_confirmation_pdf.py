"""Human-confirmation export retains the original machine result and source."""

import pymupdf
from documents.audit_pdf import render_fact_confirmation_pdf


def test_human_confirmation_report_preserves_machine_result():
    source = {
        "round_id": "round-1",
        "revision": 1,
        "machine_findings": [{"item_key": "R1", "status": "UNABLE", "suggestions": "Unknown checkbox", "locator": {"filename": "original.pdf", "rule_item_id": "R1"}}],
        "results": [
            {
                "item_key": "R1",
                "status": "PASS",
                "suggestions": "Confirmed original",
                "sources": [
                    {"file_version_id": "frozen-v1", "field": "visual.sop", "value": True, "answered_by": "Alice", "answered_at": "2026-11-07T01:00:00Z", "locator": {"kind": "pdf_page", "page": 1}}
                ],
            }
        ],
    }
    with pymupdf.open(stream=render_fact_confirmation_pdf(source), filetype="pdf") as document:
        text = "".join(page.get_text() for page in document)
        for expected in ("原机器判定", "UNABLE", "人工确认", "PASS", "Alice", "frozen-v1", "第 1 页"):
            assert expected in text
        assert "fact_revision=1" in document.metadata["subject"]
