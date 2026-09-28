"""Composition roots for application capabilities and persistence."""
from __future__ import annotations

from ppap_excel_audit_engine.checkers.rule_declarations import freeze_target_rule_execution
from ppap_product_store import AuthStore


def build_product_store(settings, *, objects=None, role: str = "api") -> AuthStore:
    """Compose persistence with the engine-owned rule-execution snapshot capability."""
    return AuthStore(
        settings,
        objects=objects,
        role=role,
        rule_execution_factory=freeze_target_rule_execution,
    )
