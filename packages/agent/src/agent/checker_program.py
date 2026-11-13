"""Develop a checker for one frozen input, then execute only the fixed program (scripts/deep_lab.py)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from audit.contracts import AuditInput, AuditResultItem, GroupContextInput, RuleInput

from agent.checker_spec import CheckerArtifact
from agent.client import DeepError
from agent.execution.checker_guest import CheckerProgramError, ProgramRunner, develop_checker, execute_fixed_checker
from agent.execution.guest_model import ModelProtocolError


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _context_items(group_context: GroupContextInput) -> dict[str, AuditInput]:
    """One identity per audit ID. by_audit_id wins when both maps carry the same ID."""
    found: dict[str, AuditInput] = {}
    ordered = list(group_context.by_audit_id.values())
    for rows in group_context.by_doc_type.values():
        ordered.extend(rows)
    for item in ordered:
        current = found.get(item.audit_id)
        if current is None:
            found[item.audit_id] = item
            continue
        if (current.file_metadata or {}) != (item.file_metadata or {}):
            raise DeepError("deep_input_invalid")
    return found


def _related(group_context: GroupContextInput | None) -> list[dict[str, Any]]:
    if group_context is None:
        return []
    items = _context_items(group_context)
    related: list[dict[str, Any]] = []
    for audit_id in sorted(items):
        item = items[audit_id]
        metadata = item.file_metadata or {}
        entry: dict[str, Any] = {
            "audit_id": audit_id,
            "file_version_id": str(metadata.get("file_version_id") or ""),
            "sha256": str(metadata.get("sha256") or ""),
            "role": str(metadata.get("role") or ""),
        }
        rule = group_context.rules_by_id.get(audit_id)
        entry["original_filename"] = str(metadata.get("original_filename") or "")
        entry["v4_source_type"] = metadata.get("v4_source_type")
        if rule is not None:
            entry["rule"] = rule.model_dump(mode="json")
        related.append(entry)
    return related


def scoped_rule(rule: RuleInput, *, file_version_id: str) -> RuleInput:
    """Bind the rule's confirmed appendix to this file and its effective clauses."""
    clause_ids = {str(item.get("id") or "").strip() for item in rule.rules}
    selected: list[dict[str, Any]] = []
    for item in rule.appendix:
        scope = item.get("scope")
        if not isinstance(scope, dict):
            selected.append(item)
            continue
        if _appendix_scope_applies(scope, file_version_id, clause_ids):
            selected.append(item)
    return rule.model_copy(update={"appendix": selected})


def _appendix_scope_applies(scope: dict[str, Any], file_version_id: str, clause_ids: set[str]) -> bool:
    """A partial scope still restricts. It must not leak to another file or clause."""
    targets = scope.get("rule_targets")
    if isinstance(targets, list):
        return any(isinstance(target, dict) and str(target.get("document_id") or "") == file_version_id and str(target.get("rule_id") or "") in clause_ids for target in targets)
    restricted = False
    document_ok = True
    rule_ok = True
    documents = scope.get("document_ids")
    rules = scope.get("rule_ids")
    if isinstance(documents, list):
        restricted = True
        document_ok = file_version_id in {str(item) for item in documents}
    if isinstance(rules, list):
        restricted = True
        rule_ok = bool(clause_ids.intersection(str(item) for item in rules))
    if not restricted:
        return True
    return document_ok and rule_ok


def input_digest(
    audit_file: AuditInput,
    rule: RuleInput,
    *,
    file_sha256: str,
    role: str,
    group_context: GroupContextInput | None = None,
) -> str:
    """Identity of this execution input. A rule ID alone is not enough."""
    rule = scoped_rule(rule, file_version_id=str((audit_file.file_metadata or {}).get("file_version_id") or audit_file.audit_id))
    payload = {
        "file_sha256": file_sha256,
        "file_version_id": str((audit_file.file_metadata or {}).get("file_version_id") or ""),
        "related": _related(group_context),
        "context_types": {kind: sorted(item.audit_id for item in rows) for kind, rows in group_context.by_doc_type.items()} if group_context is not None else {},
        "unavailable_files": group_context.unavailable_files if group_context is not None else {},
        "role": role,
        "rule": rule.model_dump(mode="json"),
    }
    return hashlib.sha256(encoded(payload)).hexdigest()


def _checked_digest(
    audit_file: AuditInput,
    rule: RuleInput,
    file_bytes: bytes,
    *,
    file_sha256: str,
    role: str,
    group_context: GroupContextInput | None,
) -> str:
    if not role.strip():
        raise DeepError("deep_input_invalid")
    if hashlib.sha256(file_bytes).hexdigest() != file_sha256:
        raise DeepError("deep_integrity_mismatch")
    return input_digest(audit_file, rule, file_sha256=file_sha256, role=role, group_context=group_context)


def adapt(
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
    *,
    file_bytes: bytes,
    file_sha256: str,
    role: str,
    executor_revision: str,
    runner: ProgramRunner,
    work: Path,
    deadline: float,
    prior: CheckerArtifact | None = None,
    reasoning_effort: str = "medium",
) -> CheckerArtifact:
    rule = scoped_rule(rule, file_version_id=str((audit_file.file_metadata or {}).get("file_version_id") or audit_file.audit_id))
    digest = _checked_digest(audit_file, rule, file_bytes, file_sha256=file_sha256, role=role, group_context=group_context)
    prior_files = {name: content.decode("utf-8") for name, content in prior.files.items()} if prior is not None else None
    try:
        artifact = develop_checker(
            audit_file=audit_file,
            rule=rule,
            group_context=group_context,
            digest=digest,
            executor_revision=executor_revision,
            runner=runner,
            work=work,
            deadline=deadline,
            prior_files=prior_files,
            reasoning_effort=reasoning_effort,
        )
    except ModelProtocolError as exc:
        raise DeepError("deep_model_protocol") from exc
    except TimeoutError as exc:
        raise DeepError("deep_timeout") from exc
    except CheckerProgramError as exc:
        raise DeepError("deep_output_invalid") from exc
    if artifact.input_digest != digest or artifact.executor_revision != executor_revision:
        raise DeepError("deep_output_identity_mismatch")
    return artifact


def execute(
    artifact: CheckerArtifact,
    audit_file: AuditInput,
    rule: RuleInput,
    group_context: GroupContextInput | None = None,
    *,
    file_bytes: bytes,
    file_sha256: str,
    role: str,
    executor_revision: str,
    work: Path,
) -> list[AuditResultItem]:
    rule = scoped_rule(rule, file_version_id=str((audit_file.file_metadata or {}).get("file_version_id") or audit_file.audit_id))
    digest = _checked_digest(audit_file, rule, file_bytes, file_sha256=file_sha256, role=role, group_context=group_context)
    if artifact.input_digest != digest or artifact.executor_revision != executor_revision:
        raise DeepError("deep_output_identity_mismatch")
    try:
        return execute_fixed_checker(
            artifact,
            audit_file=audit_file,
            rule=rule,
            group_context=group_context,
            work=work,
            digest=digest,
            executor_revision=executor_revision,
        )
    except CheckerProgramError as exc:
        raise DeepError("deep_output_invalid") from exc
