"""Run authored checker factories at the Core boundary, never business routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from audit.contracts import AuditResultItem


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


class AuthoredCheckerError(ValueError):
    """A bounded library diagnostic, without provider or original payloads."""


def _authored_error(clause: str, code: str) -> AuditResultItem:
    return AuditResultItem(classification="规则审核", item=clause, priority="P0", status="ERR", suggestions=code, clause_id=clause)


def _require_pdf_spec(audit: Any, context: Any) -> None:
    from audit.checker_lib.source import SourceAudit

    if isinstance(audit, SourceAudit) or Path(getattr(context.audit_file, "file_url", "")).suffix.lower() != ".pdf":
        return

    from documents.pdf import PdfSpec, validate_pdf_spec

    from audit.checker_lib.nio.excel_agent import ExcelAgent
    from audit.checker_lib.uaes.native import UaesSourceAgent

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
