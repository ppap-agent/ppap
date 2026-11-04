"""Bind authored task clauses to the existing UAES check and evidence lifecycle."""

from __future__ import annotations

from audit.checkers.v4.uaes.native import UaesSourceAgent
from audit.contracts import RuleItem
from audit.engine.checkers.rule_check import RuleCheck
from audit.engine.checkers.rule_declarations import discover_declared_rules


def bind_native_checks(audit: UaesSourceAgent) -> tuple[RuleCheck, ...]:
    """Only explicitly authored clause IDs execute; inherited source methods are helpers."""
    indices: dict[str, int] = {}
    for index, (name, _) in enumerate(discover_declared_rules(type(audit))):
        method = type(audit).__dict__.get(name)
        if method is None:
            continue
        ident = str(getattr(method, "__rule_clause_id__", "") or "").strip()
        if not ident or ident in indices:
            raise ValueError(f"检查入口 {name} 缺少或重复条款 ID")
        indices[ident] = index
    items = [RuleItem.model_validate({"category": "", "rule_to_check": "", "type_of_rule": "", **raw}) for raw in audit.rule.rules]
    ids = [item.id.strip() for item in items]
    if any(not ident for ident in ids) or len(set(ids)) != len(ids) or set(ids) != set(indices):
        raise ValueError("生效条款与本次检查入口必须按 ID 一一对应")

    def bound(item: RuleItem, index: int) -> RuleCheck:
        def run():
            result = audit.check(index)
            return result.model_copy(update={"priority": item.priority or "P0", "item": item.category or item.rule_to_check or item.id})

        return RuleCheck(item, run)

    return tuple(bound(item.model_copy(update={"id": ident}), indices[ident]) for item, ident in zip(items, ids, strict=True))
