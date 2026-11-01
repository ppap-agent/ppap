"""Direct audit execution from canonical routing."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from audit.contracts import (
    AuditInput,
    AuditResultItem,
    GroupContextInput,
    RuleInput,
)

from .routing import (
    get_audit_file_compatibility_error,
    get_document_type_from_rule,
    resolve_engine_agent_class,
)
from .runtime import AuditRuntime, bind_runtime

logger = logging.getLogger(__name__)


def _error_result(rule_input: RuleInput, suggestions: str) -> list[AuditResultItem]:
    return [
        AuditResultItem(
            classification="规则审核",
            item=rule_input.type or "Unknown",
            priority="P0",
            status="ERR",
            suggestions=suggestions,
        )
    ]


def _execute_agent(
    agent_cls: type[Any],
    audit_input: AuditInput,
    rule_input: RuleInput,
    group_context: Optional[GroupContextInput],
    runtime: Optional[AuditRuntime],
) -> list[AuditResultItem]:
    agent = agent_cls(audit_input, rule_input, group_context=group_context)
    if runtime is not None:
        setattr(agent, "runtime", runtime)
    try:
        checker_count = len(getattr(agent, "rule_checkers", []) or [])
        if checker_count == 0:
            checker_count = len(rule_input.rules)

        items: list[AuditResultItem] = []
        for index in range(checker_count):
            result = agent.check(index)
            if result is not None:
                items.append(AuditResultItem.model_validate(result.model_dump()))
        return items
    finally:
        close_method = getattr(agent, "close", None)
        if callable(close_method):
            try:
                close_method()
            except Exception as exc:
                logger.debug("failed to close agent resources: %s", exc)


def run_audit(
    audit_input: AuditInput,
    rule_input: RuleInput,
    group_context: Optional[GroupContextInput] = None,
    runtime: Optional[AuditRuntime] = None,
) -> List[AuditResultItem]:
    """Execute an audit through the canonical type/file/profile route."""
    compatibility_error = get_audit_file_compatibility_error(audit_input.file_url)
    if compatibility_error is not None:
        raise ValueError(compatibility_error)

    doc_type = get_document_type_from_rule(rule_input)
    if doc_type is None:
        return _error_result(
            rule_input,
            f"No agent registered for doc type: {rule_input.type}",
        )

    agent_cls = resolve_engine_agent_class(
        doc_type,
        audit_input.file_url,
        rule_input.rule_profile,
    )
    if agent_cls is None:
        return _error_result(
            rule_input,
            f"No agent registered for doc type: {rule_input.type}",
        )

    try:
        with bind_runtime(runtime):
            return _execute_agent(
                agent_cls,
                audit_input,
                rule_input,
                group_context,
                runtime,
            )
    except Exception as exc:
        logger.error("audit engine checker failed: %s", exc, exc_info=True)
        return _error_result(rule_input, f"Audit engine execution failed: {exc}")
