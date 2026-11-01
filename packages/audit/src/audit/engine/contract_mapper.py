"""Normalize engine inputs at the runtime boundary."""

from __future__ import annotations

from typing import Any

from audit.contracts import AuditInput, RuleInput
from audit.rule_profile import normalize_rule_profile


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
