"""The real OLE original reaches the neutral visual reader, without provider calls."""

import hashlib
from unittest.mock import Mock

from audit.checker_lib.visual_facts import read_original_visual_facts
from audit.contracts import AuditInput
from corpus_contracts.oracle import original


def test_real_ole_xls_dispatch_is_not_pdf_only(monkeypatch):
    monkeypatch.setenv("PPAP_MODEL_VISION_MODEL", "controlled-visual")
    monkeypatch.setenv("PPAP_MODEL_VISION_API_KEY", "controlled-key")
    monkeypatch.setenv("PPAP_MODEL_VISION_BASE_URL", "https://controlled.invalid/v1")
    monkeypatch.delenv("PPAP_PADDLE_URL", raising=False)
    monkeypatch.delenv("PPAP_PADDLE_BRIDGE_URL", raising=False)
    path = original("过程流程图.xls")
    assert path.read_bytes().startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
    calls = []

    def observe(audit, sheet, region, fields, labels, **kwargs):
        calls.append((sheet.title, region, labels))
        return dict.fromkeys(fields, True)

    monkeypatch.setattr("audit.checker_lib.visual.observe_excel_region", observe)
    audit = AuditInput(audit_id="frozen-version", file_url=str(path), file_metadata={"file_version_id": "file-version", "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    assert read_original_visual_facts(audit, {"flow_symbols": "原件流程图具有实际流程符号"}, Mock(), cache={}) == {"flow_symbols": True}
    assert calls and all(region is None and labels for _, region, labels in calls)
