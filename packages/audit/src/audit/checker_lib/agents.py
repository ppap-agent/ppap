"""Run a company's reader implementation for each clause a leaf binds.

A leaf's ``RESOURCE`` names the implementation class per original format and the
implementation method per clause. When the implementation numbers its own rules,
``implementation_rules`` gives it that numbering; findings keep the leaf's clause IDs.
Nothing is chosen by position, title or document type at run time.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from audit.contracts import AuditResultItem
from audit.engine.checkers.rule_declarations import discover_declared_rules


class _Clause:
    def __init__(self, owner: AgentAudit, clause_id: str) -> None:
        self._owner = owner
        self.rule_item = SimpleNamespace(id=clause_id)

    def check(self) -> AuditResultItem:
        return self._owner.evaluate(self.rule_item.id)


class AgentAudit:
    def __init__(self, audit_file: Any, rule: Any, group_context: Any = None, *, resource: dict[str, Any]) -> None:
        self.audit_file, self.rule, self.group_context, self.resource = audit_file, rule, group_context, resource
        rows = getattr(rule, "rules", ()) or ()
        self.checks = [_Clause(self, str(row.get("id") if isinstance(row, dict) else row.id)) for row in rows]
        self._agent: Any = None
        self._failure: BaseException | None = None

    def _implementation_rule(self) -> Any:
        rows = self.resource.get("implementation_rules")
        if not rows:
            return self.rule
        return self.rule.model_copy(update={"rules": [{**row, "priority": "P0", "type_of_rule": "text"} for row in rows]})

    def _implementation(self) -> Any:
        # Creation runs once; a failed creation is not retried for later clauses.
        if self._failure is not None:
            raise self._failure
        if self._agent is None:
            path = self.resource["carriers"].get(Path(self.audit_file.file_url).suffix.lower())
            if path is None:
                return None
            module, name = path.split(":", 1)
            try:
                cls = getattr(importlib.import_module(module), name)
                self._agent = cls(self.audit_file, self._implementation_rule(), group_context=self.group_context)
            except BaseException as exc:
                self._failure = exc
                raise
        return self._agent

    def evaluate(self, clause_id: str) -> AuditResultItem:
        method = self.resource["methods"].get(clause_id)
        agent = self._implementation() if method else None
        if method is None or agent is None:
            return AuditResultItem(classification="规则审核", item=clause_id, priority="P0", status="UNABLE", suggestions="该检查程序尚不支持此条款或原件格式。", clause_id=clause_id)
        declaration_type = getattr(type(agent), "TARGET_EXCEL_AGENT_CLASS", None) or type(agent)
        names = [name for name, _ in discover_declared_rules(declaration_type)]
        if method not in names:
            raise ValueError("checker_method_missing")
        index = names.index(method)
        source_check = getattr(agent, "check_source_rule", None)
        direct = getattr(agent, "_execute_rule_check", None)
        # The implementation owns its conversion and evidence policy; never its broad AI fallback.
        item = source_check(index) if callable(source_check) else direct(index + 1) if callable(direct) else agent.check(index)
        if item is None:
            raise ValueError("checker_missing_result")
        return AuditResultItem.model_validate(item.model_dump()).model_copy(update={"clause_id": clause_id})

    def close(self) -> None:
        close = getattr(self._agent, "close", None)
        if callable(close):
            close()
        self._agent = None
