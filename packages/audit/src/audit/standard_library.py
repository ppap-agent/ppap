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
        item = checker.check_source_rule(index)
    else:
        direct = getattr(checker, "_execute_rule_check", None)
        item = direct(index + 1) if callable(direct) else checker.check(index)
    if item is None:
        raise ValueError("standard_suite_missing_result")
    result = AuditResultItem.model_validate(item.model_dump())
    return result.model_copy(update={"clause_id": context.clause_id})


class AuthoredCheckerError(ValueError):
    """A bounded library diagnostic, without provider or original payloads."""


def _authored_error(clause: str, code: str) -> AuditResultItem:
    return AuditResultItem(classification="规则审核", item=clause, priority="P0", status="ERR", suggestions=code, clause_id=clause)


def _require_pdf_spec(audit: Any, context: Any) -> None:
    from documents.pdf import PdfSpec, validate_pdf_spec

    from audit.checkers.v3.excel_agent import ExcelAgent
    from audit.checkers.v4.uaes.native import UaesSourceAgent

    # Native readers and plain callable checkers need no grid specification.
    if not isinstance(audit, ExcelAgent) or isinstance(audit, UaesSourceAgent) or Path(context.audit_file.file_url).suffix.lower() != ".pdf":
        return
    spec = type(audit).__dict__.get("PDF_SPEC")
    if not isinstance(spec, PdfSpec):
        raise AuthoredCheckerError("authored_pdf_spec_required")
    try:
        validate_pdf_spec(spec)
    except ValueError:
        raise AuthoredCheckerError("authored_pdf_spec_invalid") from None


def check_factory(context: Any, factory: Any) -> AuditResultItem:
    """Adapt authored artifacts without retry, positional binding or extra execution.

    Diagnostics about unbound authored methods are separate from Core findings.
    Only the frozen explicit clauses can produce authoritative findings.
    """
    import inspect

    from pydantic import ValidationError

    key = ("authored-checker", context.audit_file.audit_id, context.target.binding.checker_digest)

    def create():
        signature = inspect.signature(factory)
        args: tuple[Any, ...] = (context.audit_file, context.rule, context.group_context)
        try:
            signature.bind(*args)
        except TypeError:
            args = args[:2]
            signature.bind(*args)
        audit = factory(*args)
        context.on_close(audit)
        _require_pdf_spec(audit, context)
        return audit

    audit = _once(context, key, create)
    checks = tuple(getattr(audit, "checks", ()) or ())
    if not checks:
        raise AuthoredCheckerError("authored_checker_missing_checks")
    diagnostic_key = ("authored-diagnostics", context.audit_file.audit_id)
    diagnostics = context.cache.setdefault(diagnostic_key, [])

    def diagnostic(clause: str | None, code: str) -> None:
        value = {"file_version_id": context.audit_file.audit_id, "clause_id": clause, "code": code}
        if value not in diagnostics:
            diagnostics.append(value)

    def checked(check):
        result = check.check()
        if result is None:
            return None
        try:
            return AuditResultItem.model_validate(result if isinstance(result, dict) else result.model_dump())
        except (ValidationError, AttributeError):
            raise AuthoredCheckerError("authored_checker_invalid_result") from None

    ids = [str(getattr(getattr(check, "rule_item", None), "id", "") or "") for check in checks]
    if any(hasattr(check, "rule_item") for check in checks):
        allowed = {row["id"] for row in context.rule.rules}
        for clause in ids:
            if not clause:
                diagnostic(None, "authored_checker_missing_clause_id")
            elif clause not in allowed:
                diagnostic(clause, "authored_checker_extra_clause")
        matches = [check for check, clause in zip(checks, ids) if clause == context.clause_id]
        if not matches:
            raise AuthoredCheckerError("authored_checker_missing_binding")
        if len(matches) != 1:
            raise AuthoredCheckerError("authored_checker_duplicate_binding")
        problem = getattr(matches[0], "binding_error", None)
        if problem:
            if str(problem).endswith("绑定了多个检查入口"):
                raise AuthoredCheckerError("authored_checker_duplicate_binding")
            if str(problem).endswith("没有对应的检查入口"):
                raise AuthoredCheckerError("authored_checker_missing_binding")
            raise AuthoredCheckerError("authored_checker_invalid_binding")
        item = checked(matches[0])
        if item is None:
            return _authored_error(context.clause_id, "checker_missing_result")
        if item.clause_id != context.clause_id:
            raise AuthoredCheckerError("authored_checker_result_clause_mismatch")
        return item

    # Earlier authored factories attach identity to the result only. Evaluate
    # once, retain uniquely attributed results, and reject all extra outputs.
    def collect():
        results: dict[str, AuditResultItem] = {}
        for check in checks:
            item = checked(check)
            if item is None or not item.clause_id:
                diagnostic(None, "authored_checker_missing_clause_id")
                continue
            clause = item.clause_id
            if clause not in context.target.binding.entrypoints:
                diagnostic(clause, "authored_checker_extra_clause")
            elif clause in results:
                results[clause] = _authored_error(clause, "authored_checker_duplicate_result")
            else:
                results[clause] = item
        return results

    results = _once(context, (*key, "results"), collect)
    return results.get(context.clause_id) or _authored_error(context.clause_id, "authored_checker_missing_result")


def prepare_target(context: Any, resources: dict[str, Any]) -> list[dict]:
    """Free preparation hook owned by the frozen Suite, executed only in guest.

    This reuses the original preparation algorithms, never promotes formal NG,
    and never derives the selected implementation from profile/filename routing.
    """
    from audit.precheck_contract import fact
    from audit.precheck_dependencies import dependency_facts

    entry = context.rule.preset_key
    facts = list(dependency_facts(context.audit_file, entry, context.group_context))
    implementation = resources["carriers"].get(Path(context.audit_file.file_url).suffix.lower())
    if implementation is None:
        return [*facts, fact("coverage", "not_checked", "此套件尚不支持原件载体的准备检查。")]
    module, name = implementation.split(":", 1)
    cls = getattr(importlib.import_module(module), name)
    checker = cls(context.audit_file, context.rule, group_context=context.group_context)
    context.on_close(checker)
    declarations = discover_declared_rules(cls)
    for source_id, method in getattr(cls, "PRECHECK_METHODS", {}).items():
        indices = [index for index, (name, _) in enumerate(declarations) if name == method]
        if len(indices) != 1:
            facts.append(fact(source_id, "error", "预检方法绑定无效。"))
            continue
        observed = checker.check(indices[0])
        status, evidence = getattr(observed, "status", None), getattr(observed, "evidence", None)
        mapped = "satisfied" if status == "PASS" and evidence and evidence.sources else "error" if status == "ERR" else "unknown"
        facts.append(fact(source_id, mapped, getattr(observed, "suggestions", "未取得检查结论。"), evidence=evidence.model_dump(mode="json") if evidence else None))
    if entry in {"U07", "L07", "N02"}:
        from audit.precheck_pfmea import pfmea_precheck

        facts.extend(pfmea_precheck(context.audit_file, entry, context.group_context))
    if entry in {"U08", "L08", "N03"}:
        from audit.precheck_cp import cp_precheck

        facts.extend(cp_precheck(context.audit_file, entry, context.group_context))
    custom = getattr(checker, "precheck", None)
    if custom:
        facts.extend(custom())
    return facts
