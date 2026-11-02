"""One executable check bound to one effective rule clause."""

from __future__ import annotations

from collections.abc import Callable

from audit.contracts import AuditResultItem, RuleItem


class RuleCheck:
    """Run one clause and always return a result that keeps its identity."""

    def __init__(self, rule_item: RuleItem, run: Callable[[], AuditResultItem | None], *, binding_error: str | None = None) -> None:
        self.rule_item = rule_item
        self._run = run
        self.binding_error = binding_error

    def check(self) -> AuditResultItem:
        result = self._run()
        if result is None:
            return self._missing_result()
        if result.clause_id or not self.rule_item.id:
            return result
        return result.model_copy(update={"clause_id": self.rule_item.id})

    def _missing_result(self) -> AuditResultItem:
        clause_id = self.rule_item.id
        if clause_id:
            suggestions = f"条款 {clause_id} 没有返回结果"
            item = self.rule_item.category or self.rule_item.rule_to_check or f"条款 {clause_id}"
        else:
            suggestions = "检查没有返回结果"
            item = self.rule_item.category or self.rule_item.rule_to_check or "未命名条款"
        return AuditResultItem(
            classification="规则审核",
            item=item,
            priority=self.rule_item.priority or "P0",
            status="ERR",
            suggestions=suggestions,
            clause_id=clause_id or None,
        )
