"""Native scope witnesses retain frozen original identity through public mapping.

These checks certify evidence projection, not a new business Oracle or model fact.
"""

import hashlib
import json
from pathlib import Path

import fitz
import pytest
from agent.mapping import _checker_finding
from audit.contracts import AuditEvidence, AuditInput, AuditResultItem, EvidenceSource
from audit.engine.checkers.psw_pdf_evidence import _original_pdf_sources
from audit.engine.checkers.psw_scope import _Objects

ORIGINAL = Path(__file__).resolve().parents[3] / "assets/理想/样本/模拟测试文件V2-01/16-零件提交保证书/16-零件提交保证书-上海亚大.pdf"
ORIGINAL_SHA = "24133e74c1dac80339a694863930121b9525d4b0d887f7e020b9ed82f3014c0b"
VERSION = "frozen-psw-version"


def native_heading_sources(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = AuditInput(audit_id=VERSION, file_url=str(path), file_metadata={"source_sha256": digest})
    objects = _Objects(audit, digest, {})
    sources = tuple(EvidenceSource(kind="structure", locator=json.dumps(args[0]), excerpt=args[1]) for args, _options in objects.sources if args[0].get("coverage") == "native_document_heading")
    assert {json.loads(source.locator)["page"] for source in sources} == {1, 6}
    return audit, digest, sources


def public_finding(audit, digest, sources):
    result = AuditResultItem(
        classification="projection",
        item="native scope witnesses",
        priority="P0",
        status="PASS",
        suggestions="",
        checks_performed=1,
        evidence=AuditEvidence(required="structure", used="structure", source_format="pdf", prepared_format="pdf", sources=_original_pdf_sources(audit, sources)),
    )
    return _checker_finding(result, file_version_id=VERSION, file_facts={VERSION: (Path(audit.file_url).name, "target")}, frozen_sha256={VERSION: digest})


@pytest.mark.parametrize("rotation", [0, 90])
def test_original_native_headings_project_to_the_same_frozen_pdf(tmp_path, rotation):
    original = ORIGINAL.read_bytes()
    assert hashlib.sha256(original).hexdigest() == ORIGINAL_SHA
    path = ORIGINAL
    if rotation:
        path = tmp_path / "rotated-original.pdf"
        with fitz.open(stream=original, filetype="pdf") as document:
            for number in (1, 6):
                document[number - 1].set_rotation(rotation)
            document.save(path)
    before = path.read_bytes()
    audit, digest, sources = native_heading_sources(path)
    status, _message, incomplete, public = public_finding(audit, digest, sources)
    assert status == "PASS" and incomplete is False
    assert {item["locator"]["page"] for item in public} == {1, 6}
    assert all(item["file_version_id"] == VERSION and item["filename"] == path.name and item["role"] == "target" for item in public)
    assert all(item["locator"]["kind"] == "pdf_page" and len(item["locator"]["region"]) == 4 for item in public)
    assert path.read_bytes() == before and ORIGINAL.read_bytes() == original


@pytest.mark.parametrize("corruption", ["sha", "file-version", "coordinate-system", "bounds", "page"])
def test_foreign_or_invalid_heading_sources_cannot_become_a_business_pass(corruption):
    audit, digest, sources = native_heading_sources(ORIGINAL)
    raw = json.loads(sources[0].locator)
    changes = {
        "sha": {"source_sha256": "0" * 64},
        "file-version": {"file_version_id": "outside-frozen-round"},
        "coordinate-system": {"coordinate_system": "rotated-pixels"},
        "bounds": {"bbox": [0, 0, -1, 1]},
        "page": {"page": 1000},
    }
    raw.update(changes[corruption])
    corrupted = sources[0].model_copy(update={"locator": json.dumps(raw)})
    status, message, incomplete, public = public_finding(audit, digest, (corrupted,))
    assert status == "UNABLE" and incomplete is True and public == []
    assert "本次冻结文件" in message
