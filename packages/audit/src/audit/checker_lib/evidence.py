"""Original locations and structure evidence. No second observation model."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from audit.contracts import AuditEvidence, EvidenceDiagnostic, EvidenceSource

_Format = Literal["xlsx", "xls", "xlsm", "pdf", "docx", "png", "jpg", "jpeg", "webp", "txt", "md"]


def version_id(audit_file: Any) -> str:
    meta = audit_file.file_metadata if isinstance(getattr(audit_file, "file_metadata", None), dict) else {}
    value = meta.get("file_version_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    audit_id = getattr(audit_file, "audit_id", "")
    return audit_id.strip() if isinstance(audit_id, str) else ""


def content_sha256(audit_file: Any) -> str:
    meta = audit_file.file_metadata if isinstance(getattr(audit_file, "file_metadata", None), dict) else {}
    value = meta.get("sha256")
    return value if isinstance(value, str) and len(value) == 64 else ""


def json_locator(payload: Mapping[str, str], *, file_version_id: str, source_sha256: str = "") -> str:
    body = dict(payload)
    body["file_version_id"] = file_version_id
    if source_sha256:
        body["source_sha256"] = source_sha256
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def structure_diagnostic(code: str, message: str) -> EvidenceDiagnostic:
    return EvidenceDiagnostic(code=code, stage="structure", severity="warning", message=message)


def structure_evidence(*, source_format: _Format, sources: tuple[EvidenceSource, ...], diagnostics: tuple[EvidenceDiagnostic, ...] = ()) -> AuditEvidence:
    return AuditEvidence(
        required="structure",
        used="structure" if sources else "none",
        source_format=source_format,
        prepared_format=source_format,
        sources=sources,
        diagnostics=diagnostics,
    )
