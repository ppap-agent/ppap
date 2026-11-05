"""Compatibility at the implementation boundary, never business routing.

The Suite source owns carrier and clause-to-method identities. Existing native
readers and evidence-producing checkers are reused without inheriting fallback.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from audit.contracts import AuditResultItem
from audit.engine.checkers.rule_declarations import discover_declared_rules


def check_clause(context: Any, resources: dict[str, Any]) -> AuditResultItem:
    method = resources["methods"].get(context.clause_id)
    implementation = resources["carriers"].get(Path(context.audit_file.file_url).suffix.lower())
    if method is None or implementation is None:
        return AuditResultItem(classification="规则审核", item=context.clause_id, priority="P0", status="UNABLE", suggestions="该标准套件尚不支持此条款或原件载体。", clause_id=context.clause_id)
    key = ("standard-checker", context.audit_file.audit_id, implementation)
    checker = context.cache.get(key)
    if checker is None:
        module, name = implementation.split(":", 1)
        cls = getattr(importlib.import_module(module), name)
        checker = cls(context.audit_file, context.rule, group_context=context.group_context)
        context.cache[key] = checker
        context.on_close(checker)
    # Index is an internal adapter for a named, explicit library method. It
    # never assigns business clauses by order or infers coverage from a class.
    names = [name for name, _ in discover_declared_rules(type(checker))]
    if method not in names:
        raise ValueError("standard_suite_method_missing")
    index = names.index(method)
    direct = getattr(checker, "_execute_rule_check", None)
    item = direct(index + 1) if callable(direct) else checker.check(index)
    if item is None:
        raise ValueError("standard_suite_missing_result")
    result = AuditResultItem.model_validate(item.model_dump())
    return result.model_copy(update={"clause_id": context.clause_id})
