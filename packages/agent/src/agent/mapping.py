"""Thin adapter: current RoundWork ↔ bounded Deep spec, Deep result ↔ product findings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal, cast

from audit.audits import REFERENCE, TARGET, RoundWork

from agent.client import DeepError
from agent.protocol import DeepCoverage, DeepFinding, ExecuteRequest, ExecuteResponse, FrozenFile


def spec_from_work(work: RoundWork, executor: str, executor_revision: str, protocol_revision: str) -> ExecuteRequest:
    if executor != "ppap-deep":
        raise DeepError("deep_executor_mismatch")
    files = []
    for item in work.inputs:
        if item.role not in {TARGET, REFERENCE}:
            raise DeepError("deep_input_invalid")
        files.append(
            FrozenFile(
                file_version_id=item.file_version_id,
                file_id=item.file_id,
                role=cast(Literal["target", "reference"], TARGET if item.role == TARGET else REFERENCE),
                filename=item.filename,
                sha256=item.sha256 or "",
                content_type=item.content_type or "",
                rule_version_id=item.rule_version_id,
                rule_title=item.rule_title,
                rule_body=item.rule_body,
                rule_execution=item.rule_execution,
                rule_source_file_version_id=item.rule_source_file_version_id,
            )
        )
    return ExecuteRequest(
        protocol_revision=protocol_revision,
        round_id=work.round_id,
        files=files,
        executor="ppap-deep",
        executor_revision=executor_revision,
        tenant_id=work.tenant_id,
        initiating_user_id=work.initiating_user_id,
    )


def _fresh_question_id(base: str, used: set[str]) -> str:
    candidate = base
    number = 2
    while candidate in used:
        candidate = f"{base}:{number}"
        number += 1
    return candidate


def _question_source(source: object, known_versions: set[str]) -> dict | None:
    """Keep a question source only when its file belongs to this round and the locator is typed."""
    if not isinstance(source, dict):
        return None
    document_id = str(source.get("document_id") or "").strip()
    if document_id not in known_versions:
        return None
    from audit.models import public_locator

    locator = public_locator(source.get("locator"))
    if locator is None:
        return None
    excerpt = source.get("excerpt")
    return {"document_id": document_id, "locator": locator, "excerpt": excerpt[:500] if isinstance(excerpt, str) else ""}


def _merge_clarifications(results: list, file_version_id: str, existing: list[dict], known_versions: set[str]) -> list[dict]:
    """Project checker questions into the existing interpretation contract. This is not a trace proof."""
    used = {str(item.get("id") or "") for item in existing}
    produced: dict[str, dict] = {}
    for result in results:
        fallback = str(getattr(result, "clause_id", "") or "").strip()
        for raw in getattr(result, "clarifications", ()) or []:
            if not isinstance(raw, dict):
                continue
            qid = str(raw.get("id") or "").strip()
            question = str(raw.get("question") or "").strip()
            candidates = raw.get("candidates")
            raw_sources = raw.get("sources")
            raw_sources = raw_sources if isinstance(raw_sources, list) else []
            sources = [item for item in (_question_source(source, known_versions) for source in raw_sources) if item is not None]
            if not qid or not question or not isinstance(candidates, list) or len(candidates) < 2:
                continue
            clause_id = str(raw.get("clause_id") or fallback).strip()
            item = produced.get(qid)
            if item is not None and (item["question"] != question or item["candidates"] != candidates):
                qid = _fresh_question_id(f"{qid}:{clause_id or file_version_id or 'clause'}", used | set(produced))
                item = None
            if item is None:
                public_id = qid if qid not in used else _fresh_question_id(f"{qid}:{file_version_id or 'file'}", used | set(produced))
                item = {
                    "id": public_id,
                    "input_id": file_version_id,
                    "kind": "interpretation",
                    "question": question,
                    "document_ids": [file_version_id] if file_version_id else [],
                    "rule_ids": [clause_id] if clause_id else [],
                    "candidates": candidates,
                    "sources": list(sources),
                    "scope": {
                        "document_ids": [file_version_id] if file_version_id else [],
                        "rule_ids": [clause_id] if clause_id else [],
                        "rule_targets": [{"document_id": file_version_id, "rule_id": clause_id}] if file_version_id and clause_id else [],
                    },
                }
                produced[qid] = item
                used.add(public_id)
                continue
            if clause_id and clause_id not in item["rule_ids"]:
                item["rule_ids"].append(clause_id)
                item["scope"]["rule_ids"].append(clause_id)
                if file_version_id:
                    target = {"document_id": file_version_id, "rule_id": clause_id}
                    if target not in item["scope"]["rule_targets"]:
                        item["scope"]["rule_targets"].append(target)
            for source in sources:
                if source not in item["sources"]:
                    item["sources"].append(source)
    return list(produced.values())


def checker_response(
    spec: ExecuteRequest,
    executed: list[tuple[FrozenFile, list]],
    *,
    usage: dict[str, int] | None = None,
    usage_source: Literal["unknown", "provider", "controlled"] = "controlled",
) -> ExecuteResponse:
    """Map fixed checker results into the existing product response.

    ``host_source_trace_v1`` stays reserved for trace validation. A Python
    program that ran is not that proof. Historical responses keep their own
    verification label.
    """
    from audit.contracts import AuditResultItem

    findings: list[DeepFinding] = []
    rule_items: list[dict] = []
    clarifications: list[dict] = []
    file_facts = {item.file_version_id: (item.filename, item.role) for item in spec.files}
    locator_gap = False
    checked: list[str] = []
    unchecked = [f"{item.filename}（参考资料，不单独作完整审核）" for item in spec.files if item.role == "reference"]
    for target, raw_results in executed:
        if target.role != "target":
            raise DeepError("deep_input_invalid")
        clauses = (target.rule_body or {}).get("rules") or []
        if not isinstance(clauses, list) or not clauses:
            raise DeepError("deep_rules_empty")
        expected_ids = [str(clause.get("id") or "").strip() for clause in clauses if isinstance(clause, dict)]
        if any(not clause_id for clause_id in expected_ids):
            raise DeepError("deep_input_invalid")
        grouped: dict[str, list[AuditResultItem]] = {}
        unnamed: list[AuditResultItem] = []
        parsed_results: list[AuditResultItem] = []
        for raw in raw_results:
            result = raw if isinstance(raw, AuditResultItem) else AuditResultItem.model_validate(raw)
            parsed_results.append(result)
            clause_id = str(result.clause_id or "").strip()
            if not clause_id:
                unnamed.append(result)
                continue
            grouped.setdefault(clause_id, []).append(result)
        expected_counts: dict[str, int] = {}
        for clause_id in expected_ids:
            expected_counts[clause_id] = expected_counts.get(clause_id, 0) + 1
        consumed = {clause_id: 0 for clause_id in expected_counts}
        used_keys: set[str] = set()

        def unique_item_key(base: str) -> str:
            candidate = base
            number = 2
            while candidate in used_keys:
                candidate = f"{base}:{number}"
                number += 1
            used_keys.add(candidate)
            return candidate

        for clause in clauses:
            clause_id = str(clause.get("id") or "").strip()
            bucket = grouped.get(clause_id, [])
            index = consumed.get(clause_id, 0)
            consumed[clause_id] = index + 1
            matched_result = bucket[index] if index < len(bucket) else None
            status, suggestions, evidence_incomplete, sources = _checker_finding(matched_result, file_version_id=target.file_version_id, file_facts=file_facts)
            locator_gap = locator_gap or evidence_incomplete or not sources
            item_key = unique_item_key(f"{target.file_version_id}:rule:{clause_id}")
            semantic = {
                "rule_version_id": target.rule_version_id,
                "clause_id": clause_id,
                "rule_text": clause.get("rule_to_check") or "",
                "sources": sources,
                "verification_scope": "checker_execution",
            }
            if evidence_incomplete:
                semantic["evidence_incomplete"] = True
            performed = getattr(matched_result, "checks_performed", None) if matched_result is not None else None
            if type(performed) is int and performed >= 0:
                semantic["checks_performed"] = performed
            basis = str(getattr(matched_result, "empty_scope_basis", "") or "").strip() if matched_result is not None else ""
            if basis:
                semantic["empty_scope_basis"] = basis
            scope = _finished_scan_scope(matched_result)
            if scope is not None:
                semantic["scope"] = scope
            if matched_result and matched_result.evidence is not None:
                semantic["diagnostics"] = [item.model_dump() for item in matched_result.evidence.diagnostics]
            findings.append(
                DeepFinding(
                    item_key=item_key,
                    classification=str(clause.get("category") or "客户规则"),
                    item=str(clause.get("rule_to_check") or clause_id),
                    priority=str(clause.get("priority") or (matched_result.priority if matched_result else "P1")),
                    status=status,
                    suggestions=suggestions,
                    locator={
                        "kind": "file_version",
                        "file_id": target.file_id,
                        "file_version_id": target.file_version_id,
                        "filename": target.filename,
                        "rule_item_id": clause_id,
                        "semantic": semantic,
                    },
                )
            )
            rule_items.append(
                {
                    "file_version_id": target.file_version_id,
                    "filename": target.filename,
                    "rule_version_id": target.rule_version_id,
                    "rule_item_id": clause_id,
                    "id": clause_id,
                    "category": str(clause.get("category") or "客户规则"),
                    "rule_to_check": clause.get("rule_to_check") or "",
                    "item_key": item_key,
                    "coverage": "executed" if matched_result is not None else "execution_error",
                    "status": status,
                    "verification_scope": "checker_execution",
                    **({"checks_performed": performed} if type(performed) is int and performed >= 0 else {}),
                }
            )
        duplicate_results: list[tuple[str, AuditResultItem]] = []
        extra_results: list[tuple[str, AuditResultItem]] = []
        for clause_id, bucket in grouped.items():
            start = consumed.get(clause_id, 0)
            for result in bucket[start:]:
                if clause_id in expected_counts:
                    duplicate_results.append((clause_id, result))
                else:
                    extra_results.append((clause_id, result))
        for index, result in enumerate(unnamed, start=1):
            status, suggestions, evidence_incomplete, sources = _checker_finding(result, file_version_id=target.file_version_id, file_facts=file_facts)
            locator_gap = locator_gap or evidence_incomplete or not sources
            findings.append(
                DeepFinding(
                    item_key=unique_item_key(f"{target.file_version_id}:binding:missing-clause-id-{index}"),
                    classification=result.classification or "客户规则",
                    item=result.item or "未命名条款",
                    priority=result.priority or "P1",
                    status=status,
                    suggestions=suggestions,
                    locator={
                        "kind": "file_version",
                        "file_id": target.file_id,
                        "file_version_id": target.file_version_id,
                        "filename": target.filename,
                        "rule_item_id": f"missing-clause-id-{index}",
                        "semantic": {
                            "rule_version_id": target.rule_version_id,
                            "clause_id": f"missing-clause-id-{index}",
                            "rule_text": result.item or "",
                            "sources": sources,
                            "verification_scope": "checker_execution",
                            "evidence_incomplete": True,
                            "binding": "missing_clause_id",
                        },
                    },
                )
            )
        for clause_id, result in extra_results:
            status, suggestions, evidence_incomplete, sources = _checker_finding(result, file_version_id=target.file_version_id, file_facts=file_facts)
            locator_gap = locator_gap or evidence_incomplete or not sources
            item_key = unique_item_key(f"{target.file_version_id}:rule:{clause_id}")
            findings.append(
                DeepFinding(
                    item_key=item_key,
                    classification=result.classification or "客户规则",
                    item=result.item or clause_id,
                    priority=result.priority or "P1",
                    status=status,
                    suggestions=suggestions,
                    locator={
                        "kind": "file_version",
                        "file_id": target.file_id,
                        "file_version_id": target.file_version_id,
                        "filename": target.filename,
                        "rule_item_id": clause_id,
                        "semantic": {
                            "rule_version_id": target.rule_version_id,
                            "clause_id": clause_id,
                            "rule_text": result.item or "",
                            "sources": sources,
                            "verification_scope": "checker_execution",
                            "evidence_incomplete": True,
                            "binding": "extra",
                        },
                    },
                )
            )
        for clause_id, result in duplicate_results:
            status, suggestions, evidence_incomplete, sources = _checker_finding(result, file_version_id=target.file_version_id, file_facts=file_facts)
            locator_gap = locator_gap or evidence_incomplete or not sources
            item_key = unique_item_key(f"{target.file_version_id}:rule:{clause_id}")
            findings.append(
                DeepFinding(
                    item_key=item_key,
                    classification=result.classification or "客户规则",
                    item=result.item or clause_id,
                    priority=result.priority or "P1",
                    status=status,
                    suggestions=suggestions,
                    locator={
                        "kind": "file_version",
                        "file_id": target.file_id,
                        "file_version_id": target.file_version_id,
                        "filename": target.filename,
                        "rule_item_id": clause_id,
                        "semantic": {
                            "rule_version_id": target.rule_version_id,
                            "clause_id": clause_id,
                            "rule_text": result.item or "",
                            "sources": sources,
                            "verification_scope": "checker_execution",
                            "evidence_incomplete": True,
                            "binding": "duplicate",
                        },
                    },
                )
            )
        clarifications.extend(_merge_clarifications(parsed_results, target.file_version_id, clarifications, set(file_facts)))
        if raw_results:
            checked.append(target.filename)
        else:
            unchecked.append(f"{target.filename}（审核执行失败，未执行）")
    return ExecuteResponse(
        protocol_revision=spec.protocol_revision,
        round_id=spec.round_id,
        files=list(spec.files),
        findings=findings,
        coverage=DeepCoverage(
            checked=checked,
            unchecked=unchecked,
            locator_gap=locator_gap,
            rule_items=rule_items,
            clarifications=clarifications,
            verification=None,
        ),
        note="deep_checker_audit",
        usage=dict(usage or {}),
        usage_source=usage_source,
    )


def _pdf_page_locator(locator: str) -> dict | None:
    """Read file:{version};page:N. The version is not replaced by the target file."""
    marker = ";page:"
    if not locator.startswith("file:") or marker not in locator:
        return None
    version, _, rest = locator[len("file:") :].partition(marker)
    if not version or any(character in version for character in " \t;"):
        return None
    page_text, separator, region_text = rest.partition(";region:")
    if not page_text.isascii() or not page_text.isdigit():
        return None
    try:
        page = int(page_text)
    except ValueError:
        return None
    if page < 1:
        return None
    parsed: dict = {"kind": "pdf_page", "page": page}
    if separator:
        try:
            region = json.loads(region_text)
        except json.JSONDecodeError:
            return None
        parsed["region"] = region
    from audit.models import public_locator

    if public_locator(parsed) is None:
        return None
    parsed["file_version_id"] = version
    return parsed


def _gap_blocks_pass(result) -> bool:
    evidence = getattr(result, "evidence", None)
    if evidence is None:
        return False
    for item in evidence.diagnostics:
        code = str(getattr(item, "code", "") or "")
        severity = str(getattr(item, "severity", "") or "")
        if severity == "error" or "budget" in code:
            return True
    return False


def _finished_scan_scope(result) -> dict[str, int] | None:
    """Publish only a completed row scan. Assertion counts are not a scan range."""
    raw = getattr(result, "scope", None) if result is not None else None
    if not isinstance(raw, dict) or set(raw) != {"total", "checked", "unknown"}:
        return None
    values = [raw[name] for name in ("total", "checked", "unknown")]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    total, checked, unknown = values
    if checked > total or unknown > total:
        return None
    return {"total": total, "checked": checked, "unknown": unknown}


def _checker_finding(result, *, file_version_id: str, file_facts: Mapping[str, tuple[str, str]]) -> tuple[Literal["PASS", "NG", "ERR", "UNABLE", "NA"], str, bool, list[dict]]:
    if result is None:
        return "ERR", "本项未执行，不能判为通过。", True, []
    allowed = {"PASS", "NG", "ERR", "UNABLE", "NA"}
    status = result.status if result.status in allowed else "ERR"
    suggestions = result.suggestions or ("本项没有检查结论，不能判为通过。" if status == "ERR" else "")
    performed = getattr(result, "checks_performed", None)
    pending_question = bool(getattr(result, "clarifications", None))
    if pending_question and status == "PASS":
        status = "UNABLE"
        note = "含义未确认，不能判为通过。"
        if note not in suggestions:
            suggestions = f"{suggestions}；{note}" if suggestions else note
    elif pending_question and status == "NA":
        status = "UNABLE"
        note = "含义未确认，不能判为不适用。"
        if note not in suggestions:
            suggestions = f"{suggestions}；{note}" if suggestions else note
    explicit_empty_scope = status == "PASS" and bool(str(getattr(result, "empty_scope_basis", "") or "").strip())
    if not explicit_empty_scope and status == "PASS" and (performed == 0 or performed is None):
        status = "ERR"
        suggestions = "零断言不能判为通过。" if (result.suggestions or "").strip() else "空结果不能判为通过。"
    elif status == "PASS" and _gap_blocks_pass(result):
        status = "UNABLE"
        note = "原件定位、读取或预算未完成，不能判为通过。"
        if note not in suggestions:
            suggestions = f"{suggestions}；{note}" if suggestions else note
    sources = []
    dropped_source = False
    incomplete = result.evidence is None or result.evidence.used == "none" or any(item.severity in {"error", "warning"} for item in result.evidence.diagnostics)
    if result.evidence is not None:
        for source in result.evidence.sources:
            item = _public_source(source, fallback_version=file_version_id, file_facts=file_facts)
            if item is None:
                dropped_source = True
                continue
            sources.append(item)
    if dropped_source:
        incomplete = True
        if status == "PASS":
            status = "UNABLE"
            note = "原件定位不在本次冻结文件中，不能判为通过。"
            if note not in suggestions:
                suggestions = f"{suggestions}；{note}" if suggestions else note
    return cast(Literal["PASS", "NG", "ERR", "UNABLE", "NA"], status), suggestions, incomplete, sources


def _public_source(source, *, fallback_version: str, file_facts: Mapping[str, tuple[str, str]]) -> dict | None:
    """Publish only a typed locator for a file that belongs to this round."""
    locator = source.locator
    if not isinstance(locator, str):
        return None
    parsed = _pdf_page_locator(locator) or _typed_json_locator(locator)
    if parsed is None:
        return None
    version = str(parsed.pop("file_version_id", "") or "").strip() or fallback_version
    facts = file_facts.get(version)
    if facts is None:
        return None
    filename, role = facts
    if role not in {"target", "reference"} or not str(filename or "").strip():
        return None
    excerpt = getattr(source, "excerpt", "")
    if not isinstance(excerpt, str):
        excerpt = ""
    return {
        "file_version_id": version,
        "filename": filename,
        "role": role,
        "kind": source.kind,
        "locator": parsed,
        "excerpt": excerpt[:500],
    }


_LOCATOR_FIELDS: dict[str, dict[str, type]] = {
    "xlsx_cell": {"sheet": str, "cell": str},
    "xlsx_range": {"sheet": str, "start": str, "end": str},
    "pdf_block": {"page": int, "block": int},
    "pdf_cell": {"page": int, "row": int, "column": int},
    "docx_paragraph": {"paragraph": int},
    "docx_cell": {"table": int, "row": int, "column": int},
}


def _excel_ref(value: object) -> bool:
    """Match ExcelCell: one to three letters and a row that does not start at zero."""
    if not isinstance(value, str):
        return False
    letters = 0
    while letters < len(value) and value[letters].isascii() and value[letters].isupper():
        letters += 1
    if letters < 1 or letters > 3:
        return False
    digits = value[letters:]
    return bool(digits) and digits.isdigit() and digits[0] != "0" and len(digits) <= 7


def _typed_json_locator(locator: str) -> dict | None:
    """Accept one public locator. Optional file_version_id selects a frozen file."""
    if not locator.startswith("{"):
        return None
    try:
        value = json.loads(locator)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    version = value.get("file_version_id", "")
    if "file_version_id" in value and not isinstance(version, str):
        return None
    kind = value.get("kind")
    if kind == "pdf_page":
        page = value.get("page")
        if type(page) is not int or page < 1:
            return None
        allowed = {"kind", "page"}
        parsed: dict = {"kind": "pdf_page", "page": page}
        if "region" in value:
            region = value.get("region")
            if not isinstance(region, list) or len(region) != 4 or any(item is not None and type(item) not in {int, float} or type(item) is bool for item in region):
                return None
            parsed["region"] = list(region)
            allowed.add("region")
        if "file_version_id" in value:
            allowed.add("file_version_id")
        if set(value) != allowed:
            return None
        if version.strip():
            parsed["file_version_id"] = version.strip()
        return parsed
    fields = _LOCATOR_FIELDS.get(kind) if isinstance(kind, str) else None
    if fields is None:
        return None
    allowed = {"kind", *fields}
    if "file_version_id" in value:
        allowed.add("file_version_id")
    if set(value) != allowed:
        return None
    for key, expected in fields.items():
        item = value.get(key)
        if type(item) is not expected or expected is str and not cast(str, item).strip() or expected is int and cast(int, item) < 1:
            return None
    if kind == "xlsx_cell" and not _excel_ref(value.get("cell")):
        return None
    if kind == "xlsx_range" and not (_excel_ref(value.get("start")) and _excel_ref(value.get("end"))):
        return None
    parsed = {"kind": kind, **{key: value[key] for key in fields}}
    if isinstance(version, str) and version.strip():
        parsed["file_version_id"] = version.strip()
    return parsed


def product_result(response: ExecuteResponse, work: RoundWork) -> dict:
    known = {item.file_version_id for item in work.inputs}
    if response.round_id != work.round_id:
        raise DeepError("deep_output_identity_mismatch")
    if [row.file_version_id for row in response.files] != [item.file_version_id for item in work.inputs]:
        raise DeepError("deep_output_identity_mismatch")
    findings: list[dict] = []
    seen: set[str] = set()
    for row in response.findings:
        if row.item_key in seen:
            raise DeepError("deep_output_invalid")
        seen.add(row.item_key)
        locator = row.locator
        version_id = (locator or {}).get("file_version_id")
        if version_id and version_id not in known:
            raise DeepError("deep_output_identity_mismatch")
        findings.append(row.model_dump())
    coverage = DeepCoverage.model_validate(response.coverage.model_dump()).model_dump()
    return {
        "findings": findings,
        "coverage": coverage,
        "note": response.note or "deep_audit",
        "usage": response.usage,
        "usage_source": response.usage_source,
    }


def passing_response(spec: ExecuteRequest) -> ExecuteResponse:
    """Deterministic ScriptedRuntime helper used by tests, not production Settings."""
    findings: list[DeepFinding] = []
    checked: list[str] = []
    unchecked: list[str] = []
    for item in spec.files:
        if item.role == TARGET:
            findings.append(
                DeepFinding(
                    item_key=f"target:{item.file_version_id}",
                    classification="资料",
                    item="深度审核已完成",
                    priority="P0",
                    status="PASS",
                    suggestions="",
                    locator={
                        "kind": "file_version",
                        "file_version_id": item.file_version_id,
                        "filename": item.filename,
                    },
                )
            )
            checked.append(item.filename)
        else:
            unchecked.append(f"{item.filename}（参考，未作为待审完整检查）")
    return ExecuteResponse(
        protocol_revision=spec.protocol_revision,
        round_id=spec.round_id,
        files=list(spec.files),
        findings=findings,
        coverage=DeepCoverage(checked=checked, unchecked=unchecked, locator_gap=False),
        note="deep_audit",
    )
