"""Run migrated checkers against a local ready file version. Not a product round."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ppap_core.contracts import AuditInput, AuditResultItem, RuleInput
from ppap_core.files import original_filename, sha256_bytes
from ppap_excel_audit_engine import run_audit

KNOWN_STATUS = {"PASS", "NG", "ERR"}


def normalize_finding(item: AuditResultItem) -> dict[str, object]:
    status = str(item.status or "").strip().upper()
    if status not in KNOWN_STATUS:
        status = "ERR"
    evidence = item.evidence.model_dump() if item.evidence is not None else None
    return {
        "classification": item.classification,
        "item": item.item,
        "priority": item.priority,
        "status": status,
        "suggestions": item.suggestions,
        "evidence": evidence,
    }


def findings_from_bytes(
    *,
    body: bytes,
    filename: str,
    sha256: str,
    file_version_id: str,
    product_rule_version_id: str,
    engine_rule_id: str,
    document_type: str,
    rule_profile: str,
    rules: list[dict] | None = None,
) -> list[dict[str, object]]:
    digest = sha256_bytes(body)
    if digest != sha256:
        raise ValueError("sha256_mismatch")
    name = original_filename(filename)
    suffix = Path(name).suffix or ".bin"
    with tempfile.TemporaryDirectory(prefix="ppap-audit-") as tmp:
        path = Path(tmp) / name
        if path.suffix.lower() != suffix.lower():
            path = Path(tmp) / f"input{suffix}"
        path.write_bytes(body)
        items = run_audit(
            AuditInput(
                audit_id=file_version_id,
                file_url=str(path),
                file_version_id=file_version_id,
                content_sha256=sha256,
                product_rule_version_id=product_rule_version_id,
                file_metadata={"original_filename": name},
            ),
            RuleInput(
                rule_id=engine_rule_id,
                rule_profile=rule_profile,
                type=document_type,
                rules=rules or [],
                product_rule_version_id=product_rule_version_id,
            ),
        )
    findings = [normalize_finding(item) for item in items]
    if not findings:
        findings = [
            {
                "classification": "规则审核",
                "item": document_type or "Unknown",
                "priority": "P0",
                "status": "ERR",
                "suggestions": "checker returned no findings",
                "evidence": None,
            }
        ]
    return findings
