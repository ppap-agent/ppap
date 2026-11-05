"""Direct audit execution from canonical routing."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from documents.files import get_file_extension_from_url

from audit.contracts import (
    AuditInput,
    AuditResultItem,
    GroupContextInput,
    RuleInput,
)
from audit.kinds import PpapDocumentType

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
        checks = getattr(agent, "checks", None)
        if checks is not None:
            items: list[AuditResultItem] = []
            for checker in checks:
                rule_item = getattr(checker, "rule_item", None)
                clause_id = str(getattr(rule_item, "id", "") or "").strip()
                try:
                    result = checker.check()
                    if result is None:
                        items.append(
                            AuditResultItem(
                                classification="规则审核",
                                item=clause_id or (rule_input.type or "Unknown"),
                                priority=str(getattr(rule_item, "priority", "") or "") or "P0",
                                status="ERR",
                                suggestions=f"条款 {clause_id} 没有返回结果" if clause_id else "检查没有返回结果",
                                clause_id=clause_id or None,
                            )
                        )
                        continue
                    items.append(AuditResultItem.model_validate(result.model_dump()))
                except Exception as exc:
                    if not clause_id:
                        raise
                    detail = f"{type(exc).__name__}: {exc}".replace("\n", " ")[:500]
                    category = str(getattr(rule_item, "category", "") or "")
                    text = str(getattr(rule_item, "rule_to_check", "") or "")
                    items.append(
                        AuditResultItem(
                            classification="规则审核",
                            item=category or text or f"条款 {clause_id}",
                            priority=str(getattr(rule_item, "priority", "") or "") or "P0",
                            status="ERR",
                            suggestions=f"条款 {clause_id} 技术失败: {detail}",
                            clause_id=clause_id,
                        )
                    )
            return items

        checker_count = len(getattr(agent, "rule_checkers", []) or [])
        if checker_count == 0:
            checker_count = len(rule_input.rules)

        items = []
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
    preset = None
    if rule_input.rule_profile == "v4":
        from audit.presets import preset_document_type, preset_rule

        customer = rule_input.customers[0] if len(rule_input.customers) == 1 else ""
        try:
            preset = preset_rule(customer, rule_input.preset_key)
        except ValueError:
            return _error_result(rule_input, "invalid_preset_source")
        if rule_input.type != preset_document_type(preset):
            return _error_result(rule_input, "preset_document_type_mismatch")

    compatibility_error = get_audit_file_compatibility_error(audit_input.file_url)
    photo_carrier = get_file_extension_from_url(audit_input.file_url).lower() in {".png", ".jpg", ".jpeg", ".webp"}
    if compatibility_error is not None and not (preset and (preset.customer, preset.entry_id) == ("UAES", "U13") and photo_carrier):
        raise ValueError(compatibility_error)

    doc_type = get_document_type_from_rule(rule_input)
    if doc_type is None and preset is not None:
        doc_type = PpapDocumentType.UNKNOWN
    if doc_type is None:
        return _error_result(
            rule_input,
            f"No agent registered for doc type: {rule_input.type}",
        )

    agent_cls = resolve_engine_agent_class(
        doc_type,
        audit_input.file_url,
        rule_input.rule_profile,
        preset_key=rule_input.preset_key,
        preset_customer=rule_input.customers[0] if len(rule_input.customers) == 1 else None,
    )
    if agent_cls is None:
        return _error_result(
            rule_input,
            f"No agent registered for doc type: {rule_input.type}",
        )

    try:
        from documents.ocr.paddle.reader import paddle_page_scope

        from audit.usage import track_ocr_usage

        with bind_runtime(runtime), paddle_page_scope(), track_ocr_usage():
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
