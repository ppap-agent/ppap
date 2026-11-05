"""Compatibility at the implementation boundary, never business routing.

The Suite source owns carrier and clause-to-method identities. Existing native
readers and evidence-producing checkers are reused without inheriting fallback.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable

from audit.contracts import AuditResultItem
from audit.engine.checkers.rule_declarations import discover_declared_rules


def _once(context: Any, key: tuple, operation: Callable[[], Any]) -> Any:
    """Remember both completed and interrupted calls; never retry unknown usage."""
    failure = (*key, "failure")
    if failure in context.cache:
        raise context.cache[failure]
    if key not in context.cache:
        try:
            context.cache[key] = operation()
        except BaseException as exc:
            context.cache[failure] = exc
            raise
    return context.cache[key]


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
        checker = _once(context, key, lambda: cls(context.audit_file, context.rule, group_context=context.group_context))
        context.on_close(checker)
    # Index is an internal adapter for a named, explicit library method. It
    # never assigns business clauses by order or infers coverage from a class.
    declaration_type = getattr(type(checker), "TARGET_EXCEL_AGENT_CLASS", None) or type(checker)
    names = [name for name, _ in discover_declared_rules(declaration_type)]
    if method not in names:
        raise ValueError("standard_suite_method_missing")
    index = names.index(method)
    if getattr(type(checker), "TARGET_EXCEL_AGENT_CLASS", None) is not None:
        # The configured PDF carrier owns its conversion/native evidence policy.
        # Never call its broad .check() exception fallback into generic AI.
        delegate = checker.excel_agent
        item = delegate._execute_rule_check(index + 1) if delegate is not None else checker._run_fallback_rule(index)
    else:
        direct = getattr(checker, "_execute_rule_check", None)
        item = direct(index + 1) if callable(direct) else checker.check(index)
    if item is None:
        raise ValueError("standard_suite_missing_result")
    result = AuditResultItem.model_validate(item.model_dump())
    return result.model_copy(update={"clause_id": context.clause_id})


def check_factory(context: Any, factory: Any) -> AuditResultItem:
    """Adapt already-authored clause-bound artifacts to the same Core call.

    Only existing authored artifacts need this compatibility helper. Core
    entrypoints remain plain callables and do not require a historical base.
    """
    import inspect

    key = ("authored-checker", context.audit_file.audit_id, context.target.binding.checker_digest)
    audit = context.cache.get(key)
    if audit is None:
        # Inspect once rather than retrying a factory after an internal TypeError,
        # which could repeat a model call with an unknown external outcome.
        signature = inspect.signature(factory)
        args: tuple[Any, ...] = (context.audit_file, context.rule, context.group_context)
        try:
            signature.bind(*args)
        except TypeError:
            args = args[:2]
            signature.bind(*args)
        audit = _once(context, key, lambda: factory(*args))
        context.on_close(audit)
    checks = tuple(getattr(audit, "checks", ()) or ())
    if not checks:
        raise ValueError("authored_checker_missing_checks")
    ids = [str(getattr(getattr(check, "rule_item", None), "id", "")) for check in checks]
    if all(ids):
        matches = [check for check, clause_id in zip(checks, ids) if clause_id == context.clause_id]
        if len(matches) != 1 or getattr(matches[0], "binding_error", None):
            raise ValueError("authored_checker_clause_binding_invalid")
        result = matches[0].check()
        if result is None:
            raise ValueError("authored_checker_missing_result")
        item = AuditResultItem.model_validate(result.model_dump())
        if item.clause_id != context.clause_id:
            raise ValueError("authored_checker_result_clause_mismatch")
        return item
    # Some valid authored factories only attach IDs to returned results. Execute
    # that factory's checks once, then attribute by IDs, never by list position.
    result_key = (*key, "results")

    def collect():
        results = {}
        for check in checks:
            result = check.check()
            if result is None:
                raise ValueError("authored_checker_missing_result")
            item = AuditResultItem.model_validate(result.model_dump())
            if item.clause_id not in context.target.binding.entrypoints or item.clause_id in results:
                raise ValueError("authored_checker_result_clause_mismatch")
            results[item.clause_id] = item
        if set(results) != set(context.target.binding.entrypoints):
            raise ValueError("authored_checker_result_clause_mismatch")
        return results

    return _once(context, result_key, collect)[context.clause_id]
