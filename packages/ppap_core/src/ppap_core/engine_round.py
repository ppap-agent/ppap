"""Run a Standard 审核轮次 from its selected file and rule versions."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ppap_core.audits import (
    KIND_PRESENCE,
    TARGET,
    RoundInput,
    RoundWork,
    execution_error_result,
    findings_from_engine_results,
    findings_from_rule_execution,
    presence_finding,
    unused_reference_note,
)
from ppap_core.contracts import AuditInput, GroupContextInput, RuleInput
from ppap_core.rule_coverage import COVERAGE_MAPPED, MODE_RULE_ITEMS
from ppap_core.types import PpapDocumentType


def _declarations(item: RoundInput) -> tuple[tuple[str, str], ...]:
    from ppap_excel_audit_engine.checkers.rule_declarations import declarations_for_target

    body = item.rule_body or {}
    return declarations_for_target(body.get("type") or "", item.filename, body.get("rule_profile") or "v2")


def _rule_execution(item: RoundInput) -> dict | None:
    if item.rule_execution:
        return item.rule_execution
    from ppap_excel_audit_engine.checkers.rule_declarations import freeze_target_rule_execution

    return freeze_target_rule_execution(item.rule_body, item.filename)


def builtin_rule_input(item: RoundInput) -> RuleInput:
    """Checker input for one target. Customer checklist text is not the checker list."""
    if not item.rule_version_id or item.rule_body is None:
        raise ValueError("rule_required")
    body = item.rule_body
    builtin_rules = []
    if body.get("kind") != KIND_PRESENCE:
        declarations = _declarations(item)
        if not declarations:
            raise ValueError("builtin_rules_unavailable")
        builtin_rules = [
            {"id": str(index), "category": description, "priority": "P0",
             "rule_to_check": description, "type_of_rule": "text"}
            for index, (_method, description) in enumerate(declarations, 1)
        ]
    return RuleInput(
        rule_id=item.rule_version_id,
        rule_profile=body.get("rule_profile") or "v2",
        type=body.get("type") or "",
        rules=builtin_rules,
        customers=list(body.get("customers") or []),
        models=list(body.get("models") or []),
        parts=list(body.get("parts") or []),
    )


def _audit_input(item: RoundInput, path: str) -> AuditInput:
    return AuditInput(
        audit_id=item.file_version_id,
        file_url=path,
        file_metadata={"original_filename": item.filename, "role": item.role},
        project_id="default",
    )


def _doc_type_key(audit: AuditInput) -> str | None:
    inferred = PpapDocumentType.infer_from_signals(
        filename=audit.file_metadata.get("original_filename", ""),
        minimum_score=8,
        min_margin=4,
    )
    if inferred is None or inferred is PpapDocumentType.UNKNOWN:
        return None
    return inferred.get_type_name()


def _write_temp(item: RoundInput, data: bytes) -> str:
    suffix = Path(item.filename).suffix or ".bin"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(data)
        return handle.name
    finally:
        handle.close()


def run_round_from_bytes(work: RoundWork, blobs: dict[str, bytes]) -> dict:
    from ppap_excel_audit_engine.app_runtime import run_audit_engine

    targets = [item for item in work.inputs if item.role == TARGET]
    if not targets:
        raise ValueError("target_required")
    rules_by_version = {}
    for item in targets:
        execution = _rule_execution(item)
        needs_engine = item.rule_kind() != KIND_PRESENCE and (
            not execution
            or execution.get("mode") != MODE_RULE_ITEMS
            or any(row.get("coverage") == COVERAGE_MAPPED for row in execution.get("items") or [])
        )
        if needs_engine:
            rules_by_version[item.file_version_id] = builtin_rule_input(item)

    temps: list[str] = []
    try:
        audits_by_version: dict[str, AuditInput] = {}
        by_audit_id: dict[str, AuditInput] = {}
        by_doc_type: dict[str, list[AuditInput]] = {}
        reference_notes: list[str] = []
        for item in work.inputs:
            data = blobs.get(item.file_version_id)
            if data is None:
                if item.role == TARGET:
                    raise ValueError("missing_file")
                reference_notes.append(unused_reference_note(item.filename, "无法加载"))
                continue
            path = _write_temp(item, data)
            temps.append(path)
            audit = _audit_input(item, path)
            audits_by_version[item.file_version_id] = audit
            by_audit_id[item.file_version_id] = audit
            if item.role == TARGET:
                rule = rules_by_version.get(item.file_version_id)
                type_key = rule.type if rule is not None else (item.rule_body or {}).get("type")
            else:
                type_key = _doc_type_key(audit)
            if type_key:
                by_doc_type.setdefault(type_key, []).append(audit)
            if item.role != TARGET:
                if type_key:
                    reference_notes.append(unused_reference_note(item.filename))
                else:
                    reference_notes.append(unused_reference_note(item.filename, "未能识别文档类型，未作为待审完整检查"))

        group_context = GroupContextInput(
            task_group_id=work.round_id,
            by_audit_id=by_audit_id,
            by_doc_type=by_doc_type,
            rules_by_id={rule.rule_id: rule for rule in rules_by_version.values()},
        )
        findings: list[dict] = []
        checked: list[str] = []
        locator_gap = False
        rule_items: list[dict] = []
        item_notes: list[str] = []
        for target in targets:
            if target.rule_kind() == KIND_PRESENCE:
                findings.append(presence_finding(target))
                checked.append(target.filename)
                continue
            execution = _rule_execution(target)
            audit = audits_by_version[target.file_version_id]
            if execution and execution.get("mode") == MODE_RULE_ITEMS:
                mapped_items = [row for row in execution.get("items") or [] if row.get("coverage") == COVERAGE_MAPPED]
                results = (
                    run_audit_engine(audit, rules_by_version[target.file_version_id], group_context=group_context)
                    if mapped_items else []
                )
                projected = findings_from_rule_execution(results, target=target, execution=execution)
            else:
                results = run_audit_engine(audit, rules_by_version[target.file_version_id], group_context=group_context)
                projected = findings_from_engine_results(results, target=target)
            findings.extend(projected["findings"])
            coverage = projected.get("coverage") or {}
            if projected["findings"] or (execution and execution.get("mode") == MODE_RULE_ITEMS):
                checked.append(target.filename)
            locator_gap = locator_gap or bool(coverage.get("locator_gap"))
            rule_items.extend(coverage.get("rule_items") or [])
            item_notes.extend(coverage.get("unchecked") or [])
        if not findings and not rule_items:
            return execution_error_result("empty_engine_result")
        return {
            "findings": findings,
            "coverage": {
                "checked": checked,
                "unchecked": reference_notes + item_notes,
                "locator_gap": locator_gap,
                "rule_items": rule_items,
            },
            "note": "in-repo engine",
        }
    finally:
        for path in temps:
            Path(path).unlink(missing_ok=True)
