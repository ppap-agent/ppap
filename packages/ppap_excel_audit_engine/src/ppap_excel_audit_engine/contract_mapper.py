"""Canonical mapping between persistence objects and audit contracts."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote, urlparse

from ppap_core.contracts import AuditInput, RuleInput
from ppap_core.rule_profile import normalize_rule_profile
from ppap_core.types import PpapDocumentType


def to_audit_input(value: Any) -> AuditInput:
    if isinstance(value, AuditInput):
        return value
    return AuditInput(
        audit_id=str(getattr(value, "audit_id", "")),
        file_url=str(getattr(value, "file_url", "")),
        customer=getattr(value, "customer", None),
        model=getattr(value, "model", None),
        part=getattr(value, "part", None),
        content=getattr(value, "content", None),
        file_metadata=dict(getattr(value, "file_metadata", {}) or {}),
        project_id=str(getattr(value, "project_id", "default") or "default"),
    )


def to_rule_input(value: Any) -> RuleInput:
    if isinstance(value, RuleInput):
        return value
    return RuleInput(
        rule_id=str(getattr(value, "rule_id", "")),
        rule_profile=normalize_rule_profile(getattr(value, "rule_profile", "v2")),
        type=str(getattr(value, "type", "") or ""),
        rules=list(getattr(value, "rules", []) or []),
        customers=list(getattr(value, "customers", []) or []),
        models=list(getattr(value, "models", []) or []),
        parts=list(getattr(value, "parts", []) or []),
    )


def dump_audit_snapshot(value: Any) -> dict[str, Any]:
    return to_audit_input(value).model_dump()


def dump_rule_snapshot(value: Any) -> dict[str, Any]:
    return to_rule_input(value).model_dump()


def load_audit_snapshot(snapshot: Any) -> AuditInput | None:
    if not isinstance(snapshot, dict):
        return None
    try:
        return AuditInput.model_validate(snapshot)
    except Exception:
        return None


def load_rule_snapshot(snapshot: Any) -> RuleInput | None:
    if not isinstance(snapshot, dict):
        return None
    try:
        return RuleInput.model_validate(snapshot)
    except Exception:
        return None


def infer_document_type(value: Any) -> PpapDocumentType | None:
    audit_input = to_audit_input(value)
    filename = unquote(os.path.basename(urlparse(audit_input.file_url).path)) if audit_input.file_url else ""
    doc_type = PpapDocumentType.infer_from_signals(
        filename=filename,
        content=audit_input.content or "",
        minimum_score=8,
        min_margin=4,
    )
    return None if doc_type == PpapDocumentType.UNKNOWN else doc_type
