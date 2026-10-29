"""Validate decoded artifacts before promotion; no Office/PDF parser runs here."""

from __future__ import annotations

from typing import Any

from .evidence import EvidenceBudget, resolve, resolve_optional
from .models import AuditReport, CompiledRulePlan, PlannedRule, RulePlan
from .protocol import CompileValidationReason, ResultBundle, RuleRef, SubmissionSpec
from .trace import validate_trace


def clause_source_error(rule: RuleRef, planned: PlannedRule, source: dict[str, Any], budget: EvidenceBudget) -> CompileValidationReason | None:
    """Check clause ownership and readable evidence, not predicate equivalence."""
    locator = planned.source_locator
    clause = next((item for index, item in enumerate(rule.clauses, 1) if str(item.get("id") or index) == planned.id), None)
    if clause is None:
        return "source_locator_unresolved"
    if rule.format == ".json":
        if locator.kind != "published_clause" or locator.clause_id != planned.id:
            return "source_locator_unresolved"
    elif locator.model_dump(mode="json") not in clause.get("source_locators", []):
        return "source_locator_unresolved"
    try:
        observed = resolve(source, locator, budget)
    except ValueError:
        if budget.remaining < 0:
            raise
        return "source_locator_unresolved"
    if not observed.known:
        return "source_locator_unresolved"
    if not any(value.strip() for value in observed.values):
        return "source_locator_empty"
    return None


def validate_report(report: AuditReport, plans: dict[str, RulePlan], documents: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    expected = {(audit_id, rule.id) for audit_id, plan in plans.items() for rule in plan.rules}
    actual = [(check.audit_id, check.rule_id) for check in report.checks]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("审核结果缺项、重复或包含未请求的规则")
    budget = EvidenceBudget()
    evidence_gaps: set[tuple[str, str]] = set()
    for check in report.checks:
        if not check.reason.strip() or check.checked > check.total or check.unknown > check.total:
            raise ValueError("审核结果缺少原因或范围计数无效")
        if check.status == "PASS" and (check.unknown or check.total == 0 or check.checked != check.total):
            raise ValueError("PASS 缺少完整范围覆盖")
        if check.status != "UNABLE" and not check.evidence:
            evidence_gaps.add((check.audit_id, check.rule_id))
        if check.status == "UNABLE" and check.diagnostic is None:
            raise ValueError("UNABLE 缺少结构化原因")
        for citation in check.evidence:
            if citation.document_id not in documents:
                raise ValueError("证据来源不属于本次 submission")
            if resolve_optional(documents[citation.document_id], citation.locator, budget) is None:
                evidence_gaps.add((check.audit_id, check.rule_id))
    return evidence_gaps


def validate_snapshot(snapshot: dict[str, Any], sha256: str, file_format: str) -> None:
    source = snapshot.get("source", {})
    if not isinstance(source, dict) or source.get("sha256") != sha256 or source.get("format") != file_format:
        raise ValueError("Snapshot source version mismatch")
    if file_format == ".json":
        body = snapshot.get("rule_body")
        clauses = snapshot.get("clauses")
        if (
            snapshot.get("kind") != "published_rule"
            or not isinstance(snapshot.get("rule_version_id"), str)
            or not snapshot["rule_version_id"].strip()
            or not isinstance(body, dict)
            or not isinstance(body.get("rules"), list)
            or not isinstance(clauses, list)
            or not 1 <= len(clauses) <= 256
            or clauses != body["rules"]
        ):
            raise ValueError("Invalid published rule snapshot")
        ids = set()
        for row in clauses:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"].strip()
                or not isinstance(row.get("rule_to_check"), str)
                or not row["rule_to_check"].strip()
                or row["id"] in ids
            ):  # noqa: E501
                raise ValueError("Invalid published rule clause")
            ids.add(row["id"])
        if "origin" in snapshot and (not isinstance(snapshot["origin"], dict) or not isinstance(snapshot["origin"].get("file_version_id"), str) or not snapshot["origin"]["file_version_id"].strip()):
            raise ValueError("Invalid published rule provenance")
        return
    tables = snapshot.get("tables", [])
    if not isinstance(tables, list) or len(tables) > 256 or len({table["id"] for table in tables}) != len(tables):
        raise ValueError("Invalid snapshot tables")
    cells = 0
    cell_states = {"known", "unknown_visual_evidence", "unknown_uncovered_grid", "unknown_geometry"} if file_format == ".pdf" else {"known", "unknown_formula_cache", "unknown_excel_error"}
    for table in tables:
        rows, cols = table["rows"], table["cols"]
        if type(rows) is not int or type(cols) is not int or not 0 <= rows <= 100000 or not 0 <= cols <= 16384:
            raise ValueError("Invalid snapshot dimensions")
        if not isinstance(table["cells"], dict):
            raise ValueError("Invalid snapshot cells")
        cells += len(table["cells"])
        if cells > 1000000:
            raise ValueError("Snapshot cell limit")
        if len(table.get("merges", [])) > 4096:
            raise ValueError("Snapshot merge limit")
        for cell in table["cells"].values():
            if cell.get("state") not in cell_states or not isinstance(cell.get("value"), (str, int, float, bool, type(None))):
                raise ValueError("Invalid cell observation")
        for span in table.get("merges", []):
            r1, c1, r2, c2 = span
            if any(type(value) is not int for value in span) or not (1 <= r1 <= r2 <= rows and 1 <= c1 <= c2 <= cols):
                raise ValueError("Invalid merge span")
    pages = snapshot.get("pages", [])
    if len(pages) > 1000 or len(snapshot.get("paragraphs", [])) > 100000:
        raise ValueError("Snapshot page/paragraph limit")
    for index, page in enumerate(pages, 1):
        if page["page"] != index or len(page["blocks"]) > 100000 or any(not isinstance(block["text"], str) for block in page["blocks"]):
            raise ValueError("Invalid PDF blocks")
    if file_format == ".pdf":
        from .pdf_tables import validate_pdf_tables

        validate_pdf_tables(snapshot)


def validate_bundle(bundle: ResultBundle, spec: SubmissionSpec) -> set[tuple[str, str]]:
    if bundle.execution_id != spec.execution_id or bundle.semantic_engine_revision != spec.semantic_engine_revision:
        raise ValueError("Execution identity mismatch")
    if set(bundle.documents) != {item.audit_id for item in spec.documents}:
        raise ValueError("Result document set differs from frozen input")
    if set(bundle.rule_sources) != {item.rule_id for item in spec.rules} or set(bundle.plans) & set(bundle.failed_rules) or set(bundle.plans) | set(bundle.failed_rules) != set(bundle.rule_sources):
        raise ValueError("Result rule set differs from frozen input")
    for document in spec.documents:
        validate_snapshot(bundle.documents[document.audit_id], document.sha256, document.format)
    source_budget = EvidenceBudget()
    for rule in spec.rules:
        snapshot = bundle.rule_sources[rule.rule_id]
        validate_snapshot(snapshot, rule.sha256, rule.format)
        if rule.format == ".json" and (snapshot["clauses"] != rule.clauses or (rule.rule_version_id is not None and snapshot["rule_version_id"] != rule.rule_version_id)):
            raise ValueError("Published rule version or clause set mismatch")
        failure = bundle.failed_rules.get(rule.rule_id)
        if failure is not None:
            if (failure.rule_id, failure.source_sha256, failure.plan_revision, failure.engine_revision) != (rule.rule_id, rule.sha256, rule.plan_revision, spec.semantic_engine_revision):
                raise ValueError("Failed rule identity differs from frozen input")
            if failure.clause_id is not None and failure.clause_id not in {str(clause.get("id") or index) for index, clause in enumerate(rule.clauses, 1)}:
                raise ValueError("Failed clause does not belong to frozen rule")
            continue
        plan = bundle.plans[rule.rule_id]
        CompiledRulePlan.model_validate(plan.model_dump(mode="json"))
        if plan.appendix != snapshot.get("rule_body", {}).get("appendix", []):
            raise ValueError("Plan confirmations differ from frozen rule inputs")
        ids = [str(item.get("id") or index) for index, item in enumerate(rule.clauses, 1)]
        if plan.source_sha256 != rule.sha256 or plan.plan_revision != rule.plan_revision or [item.id for item in plan.rules] != ids:
            raise ValueError("Rule plan version or coverage mismatch")
        for planned in plan.rules:
            if clause_source_error(rule, planned, snapshot, source_budget) is not None:
                raise ValueError("Rule plan source does not identify readable text owned by its frozen clause")
    plans = {audit_id: bundle.plans[rule_id] for audit_id, rule_id in spec.targets.items() if rule_id in bundle.plans}
    evidence_gaps = validate_report(bundle.report, plans, bundle.documents)
    evidence_gaps.update(validate_trace(bundle.trace, bundle.report, bundle.documents, plans))
    return evidence_gaps
