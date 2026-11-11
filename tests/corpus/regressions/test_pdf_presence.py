"""A long original report cannot be reduced to its boundary pages."""

import hashlib
from pathlib import Path

import fitz
from audit.contracts import AuditInput
from audit.engine.checkers.pdf_presence import read_pdf_presence


def test_original_report_scans_every_page_when_no_witness_is_established(monkeypatch):
    source = Path(__file__).resolve().parents[3] / "assets/li/samples/extra/DV报告_PV报告_试验报告/W02管路PV试验报告2025052803_QFZ.pdf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert digest == "22ff4c7f35e508e59c34d58e668b878b23a5575a3e51a0116d0e9ea240ec232d"
    with fitz.open(source) as document:
        count = len(document)
    pages = []

    def unknown(audit, fields, witness, *, source_pages, **kwargs):
        assert audit.file_url == str(source)
        assert set(fields) == {"seal"}
        assert len(source_pages) == 1
        pages.extend(source_pages)
        # No fabricated observation from the human business Oracle.
        return {"seal": None}

    monkeypatch.setattr("audit.engine.checkers.visual_facts.read_original_pdf_visual_facts", unknown)
    observed = read_pdf_presence(
        AuditInput(audit_id="frozen-long-report", file_url=str(source), file_metadata={"source_sha256": digest}),
        field="seal",
        description="实际印章存在",
        witness=lambda *args, **kwargs: None,
        cache={},
        operation="dpvr_seal",
    )
    assert observed is None
    assert pages == list(range(1, count + 1))
    assert count == 146
