"""Bound clauses of one predefined directory leaf.

This leaf implements no evidence reader. Every bound clause is UNABLE.
Unknown is not a pass. This file does not call another checker version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from audit.checker_lib.evidence import structure_diagnostic, structure_evidence
from audit.contracts import AuditEvidence, AuditResultItem
from resources import RESOURCE

_BOUNDARY = "本目录 Checker 没有读取本条款证据的检查，不能判定为通过。"
_Format = Literal["xlsx", "xls", "xlsm", "pdf", "docx", "png", "jpg", "jpeg", "webp", "txt", "md"]
_FORMATS: dict[str, _Format] = {
    ".xlsx": "xlsx",
    ".xlsm": "xlsm",
    ".xls": "xls",
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".txt": "txt",
    ".md": "md",
}


def create_audit(audit_file, rule, group_context=None):
    return _BoundLeaf(audit_file, rule, group_context)


class _Clause:
    def __init__(self, audit: _BoundLeaf, clause_id: str):
        self.rule_item = type("_RuleItem", (), {"id": clause_id})()
        self._audit = audit
        self._clause_id = clause_id

    def check(self) -> AuditResultItem:
        return self._audit.evaluate(self._clause_id)


class _BoundLeaf:
    def __init__(self, audit_file, rule, group_context):
        self.audit_file = audit_file
        self.rule = rule
        self._group_context = group_context
        self.checks = [_Clause(self, clause_id) for clause_id in _clause_ids(rule)]

    def close(self) -> None:
        return None

    def evaluate(self, clause_id: str) -> AuditResultItem:
        # RESOURCE["clauses"] only names readers. This file implements none.
        # A named reader without code here stays UNABLE. It is not a pass.
        message = f"{RESOURCE['customer']}/{RESOURCE['preset_key']}：{_BOUNDARY}"
        return self._item(clause_id, "UNABLE", message, checks_performed=0, evidence=self._boundary(message))

    def _boundary(self, message: str) -> AuditEvidence | None:
        suffix = Path(str(getattr(self.audit_file, "file_url", "") or "")).suffix.lower()
        source_format = _FORMATS.get(suffix)
        if source_format is None:
            return None
        return structure_evidence(source_format=source_format, sources=(), diagnostics=(structure_diagnostic("evidence_reader_absent", message),))

    def _item(self, clause_id: str, status: str, suggestion: str, *, evidence: AuditEvidence | None, checks_performed: int) -> AuditResultItem:
        category = clause_id
        for row in getattr(self.rule, "rules", ()) or ():
            row_id = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
            if row_id == clause_id:
                raw = row.get("category") if isinstance(row, dict) else getattr(row, "category", None)
                category = str(raw or clause_id)
                break
        return AuditResultItem(
            classification="规则审核",
            item=category,
            priority="P0",
            status=status,
            suggestions=suggestion,
            evidence=evidence,
            clause_id=clause_id,
            checks_performed=checks_performed,
        )


def _clause_ids(rule) -> list[str]:
    found: list[str] = []
    for row in getattr(rule, "rules", ()) or ():
        clause_id = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        if isinstance(clause_id, str) and clause_id and clause_id not in found:
            found.append(clause_id)
    return found
