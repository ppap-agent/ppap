"""Bounded, replayable table predicates and per-record semantic observations."""

from __future__ import annotations

import re
from itertools import islice
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .document_tools import col_number, effective, ref
from .evidence import EvidenceBudget, resolve_optional
from .models import Artifact, AuditReport, Check, Citation, Diagnostic, ExcelCell, PlannedRule, Predicate, QueryProgram, RulePlan, Scalar, SemanticProgram, WordCell

TableRowLocator = Annotated[ExcelCell | WordCell, Field(discriminator="kind")]


class ExcludedRow(Artifact):
    row: int = Field(ge=1, le=100000, strict=True)
    reason_type: Literal["repeated_header", "footer", "revision_history", "non_record", "other"]
    evidence_locator: TableRowLocator
    reason: str = Field(min_length=1)


class Binding(Artifact):
    id: str = Field(min_length=1)
    document_id: str
    source_sha256: str
    table: str
    header_rows: list[int] = Field(min_length=1, max_length=32)
    first_row: int = Field(ge=1, le=100000, strict=True)
    last_row: int = Field(ge=1, le=100000, strict=True)
    columns: dict[str, str] = Field(min_length=1, max_length=64)
    excluded_rows: list[ExcludedRow] = Field(default_factory=list, max_length=4096)
    mapping_basis: str = Field(min_length=1)
    boundary_basis: str = Field(min_length=1)


class QueryProof(Artifact):
    kind: Literal["query"]
    audit_id: str
    rule_id: str
    binding_id: str


class Observation(Artifact):
    record_id: str = Field(min_length=1, max_length=256)
    role: Literal["prerequisite", "applicable", "excluded"]
    applicability_basis: str | None = None
    exclusion_basis: str | None = None
    status: Literal["PASS", "NG", "UNABLE", "NA"]
    reason: str = Field(min_length=1)
    evidence: list[Citation] = Field(default_factory=list, max_length=64)
    diagnostic: Diagnostic | None = None

    @model_validator(mode="after")
    def explained_role(self) -> Observation:
        if self.role == "applicable" and (not (self.applicability_basis or "").strip() or self.exclusion_basis is not None or self.status == "NA"):
            raise ValueError("Applicable record needs its rule-trigger basis")
        if self.role == "excluded" and (not (self.exclusion_basis or "").strip() or self.applicability_basis is not None or self.status not in {"NA", "UNABLE"}):
            raise ValueError("Excluded record needs its non-applicability basis")
        if self.role == "prerequisite" and (self.applicability_basis is not None or self.exclusion_basis is not None):
            raise ValueError("Prerequisite cannot claim a row applicability basis")
        return self


class SemanticProof(Artifact):
    kind: Literal["semantic"]
    audit_id: str
    rule_id: str
    scope_basis: str = Field(min_length=1)
    records: list[Observation] = Field(min_length=1, max_length=16384)


Proof = Annotated[QueryProof | SemanticProof, Field(discriminator="kind")]


class AuditTrace(Artifact):
    bindings: list[Binding] = Field(default_factory=list, max_length=256)
    proofs: list[Proof] = Field(max_length=16384)


def semantic_summary(proof: SemanticProof, planned: PlannedRule) -> tuple[Literal["PASS", "NG", "UNABLE", "NA"], int, int, int]:
    """Aggregate observations without treating passed prerequisites as work rows."""
    total = len(proof.records)
    unknown = sum(record.status == "UNABLE" for record in proof.records)
    statuses = {record.status for record in proof.records}
    applicable = any(record.role == "applicable" for record in proof.records)
    if "NG" in statuses:
        status: Literal["PASS", "NG", "UNABLE", "NA"] = "NG"
    elif unknown:
        status = "UNABLE"
    elif not applicable:
        status = planned.empty_scope_status
    else:
        status = "PASS"
    return status, total, total - unknown, unknown


def predicate_result(predicate: Predicate, value: Scalar) -> bool | None:
    if predicate.op in {"empty", "not_empty"}:
        empty = value is None or (isinstance(value, str) and not value.strip())
        return empty if predicate.op == "empty" else not empty
    if predicate.op == "eq":
        return type(value) is type(predicate.value) and value == predicate.value
    if predicate.op == "ne":
        return not (type(value) is type(predicate.value) and value == predicate.value)
    if predicate.op == "in":
        return any(type(value) is type(candidate) and value == candidate for candidate in predicate.values)
    if type(value) not in (int, float) or type(predicate.value) not in (int, float):
        return None
    left, right = float(str(value)), float(str(predicate.value))
    return {"gt": left > right, "ge": left >= right, "lt": left < right, "le": left <= right}[predicate.op]


def query_counts(program: QueryProgram, binding: Binding, document: dict[str, Any], budget: EvidenceBudget) -> tuple[Literal["PASS", "NG", "UNABLE", "NA"], int, int, int, dict[int, set[int]]]:
    if not binding.mapping_basis.strip() or not binding.boundary_basis.strip():
        raise ValueError("Binding lacks scope/mapping justification")
    if document["source"]["sha256"] != binding.source_sha256:
        raise ValueError("Binding source version mismatch")
    table = next((table for table in document.get("tables", []) if table["id"] == binding.table), None)
    if table is None or not 1 <= binding.first_row <= binding.last_row <= table["rows"]:
        raise ValueError("Binding outside observed table")
    columns = {field: col_number(column) for field, column in binding.columns.items()}
    if any(column > table["cols"] for column in columns.values()) or any(item.field not in columns for item in program.predicates):
        raise ValueError("Binding field outside observed table")
    if any(type(row) is not int or not 1 <= row <= table["rows"] or binding.first_row <= row <= binding.last_row for row in binding.header_rows):
        raise ValueError("Header rows overlap the body or exceed the table")
    excluded: set[int] = set()
    if len({item.row for item in binding.excluded_rows}) != len(binding.excluded_rows):
        raise ValueError("Duplicate excluded row")
    for item in binding.excluded_rows:
        number = item.row
        if not binding.first_row <= number <= binding.last_row:
            raise ValueError("Invalid excluded row")
        locator = item.evidence_locator.model_dump()
        kind = locator.get("kind")
        locator_row = locator.get("row") if kind == "docx_cell" else int(re.sub("[A-Z]", "", str(locator.get("cell", "0")))) if kind == "xlsx_cell" else None
        locator_table = f"table:{locator.get('table')}" if kind == "docx_cell" else locator.get("sheet")
        locator_column = locator.get("column") if kind == "docx_cell" else col_number(re.sub("[0-9]", "", str(locator.get("cell", ""))))
        if locator_row != number or locator_table != binding.table or type(locator_column) is not int or not 1 <= locator_column <= table["cols"]:
            raise ValueError("Excluded row evidence does not identify the excluded record")
        excluded.add(number)
    total = unknown = matched = 0
    failed: dict[int, set[int]] = {}
    for row in range(binding.first_row, binding.last_row + 1):
        budget.consume(1)
        if row in excluded:
            continue
        total += 1
        results: list[bool | None] = []
        for predicate in program.predicates:
            budget.consume(len(table.get("merges", [])) + 1)
            observation = effective(table, row, columns[predicate.field])
            results.append(predicate_result(predicate, observation["value"]) if observation["state"] == "known" else None)
        if False in results:
            failed[row] = {columns[predicate.field] for predicate, result in zip(program.predicates, results, strict=True) if result is False}
        elif None in results:
            unknown += 1
        else:
            matched += 1
    status: Literal["PASS", "NG", "UNABLE", "NA"]
    if total == 0:
        status = "UNABLE"
    elif program.quantifier == "forall":
        status = "NG" if failed else "UNABLE" if unknown else "PASS"
    else:
        status = "PASS" if matched else "UNABLE" if unknown else "NG"
    # Public PASS requires no unknown observations, including EXISTS queries.
    if status == "PASS" and unknown:
        status = "UNABLE"
    return status, total, total - unknown, unknown, failed


def _query_support(
    program: QueryProgram, binding: Binding, document: dict[str, Any], status: str, total: int, failures: dict[int, set[int]], budget: EvidenceBudget
) -> tuple[list[Citation], Diagnostic | None]:
    """Locate replayed facts; no model-authored aggregate or inferred cause."""
    cells: list[tuple[int, int]] = []
    diagnostic: Diagnostic | None = None
    if status == "NG":
        cells = list(islice(((row, column) for row, columns in failures.items() for column in sorted(columns)), 64))
    elif not total:
        return [Citation(document_id=binding.document_id, locator=item.evidence_locator) for item in binding.excluded_rows[:64]], "rule_ambiguity"
    else:
        table = next(table for table in document["tables"] if table["id"] == binding.table)
        excluded = {item.row for item in binding.excluded_rows}
        columns = [col_number(binding.columns[item.field]) for item in program.predicates]
        for row in range(binding.first_row, binding.last_row + 1):
            budget.consume(1)
            if row in excluded:
                continue
            observations = []
            results = []
            for predicate, column in zip(program.predicates, columns, strict=True):
                budget.consume(len(table.get("merges", [])) + 1)
                observation = effective(table, row, column)
                observations.append(observation)
                results.append(predicate_result(predicate, observation["value"]) if observation["state"] == "known" else None)
            if status == "PASS" and all(result is True for result in results):
                cells = [(row, column) for column in columns]
                break
            if status == "UNABLE" and False not in results and None in results:
                cells = [(row, column) for column, result in zip(columns, results, strict=True) if result is None]
                first_unknown = observations[results.index(None)]
                diagnostic = "parser_unknown" if first_unknown["state"] != "known" else "unsupported_structure"
                break
    evidence = []
    for row, column in cells:
        locator = (
            WordCell(kind="docx_cell", table=int(binding.table.removeprefix("table:")), row=row, column=column)
            if document["source"]["format"] == ".docx"
            else ExcelCell(kind="xlsx_cell", sheet=binding.table, cell=ref(row, column))
        )
        evidence.append(Citation(document_id=binding.document_id, locator=locator))
    return evidence, diagnostic


def _semantic_support(proof: SemanticProof, planned: PlannedRule, status: str, unknown: int) -> tuple[str, list[Citation], Diagnostic | None]:
    for record in proof.records:
        if record.status == "UNABLE" and record.diagnostic is None:
            raise ValueError(f"UNABLE observation lacks diagnostic: {proof.audit_id}/{proof.rule_id}/{record.record_id}")
        if record.status != "UNABLE" and record.diagnostic is not None:
            raise ValueError(f"Known observation has an UNABLE diagnostic: {proof.audit_id}/{proof.rule_id}/{record.record_id}")
    empty_scope = not any(record.role == "applicable" for record in proof.records) and not unknown and status != "NG"
    supporting = [record for record in proof.records if record.status == status or empty_scope]
    reason = f"未发现适用记录；{planned.empty_scope_basis}" if empty_scope else "；".join(record.reason for record in supporting[:3])
    if unknown and status != "UNABLE":
        unresolved = next(record for record in proof.records if record.status == "UNABLE")
        reason += f"；另有 {unknown} 项未知：{unresolved.reason}"
    if len(supporting) > 3:
        reason += f"；其余 {len(supporting) - 3} 项同类观察见执行记录"
    if status == "UNABLE" and not empty_scope:
        reason += f"；共 {unknown} 项未知"
    evidence: dict[str, Citation] = {}
    for record in supporting:
        for citation in record.evidence:
            evidence.setdefault(citation.model_dump_json(), citation)
            if len(evidence) == 64:
                break
        if len(evidence) == 64:
            break
    diagnostic = "rule_ambiguity" if empty_scope and status == "UNABLE" else next((record.diagnostic for record in supporting if record.status == "UNABLE"), None)
    return reason, list(evidence.values()), diagnostic


def derive_report(trace: AuditTrace, documents: dict[str, dict[str, Any]], plans: dict[str, RulePlan]) -> AuditReport:
    """Project execution facts into the existing report; bundle validation remains mandatory."""
    expected = {(audit_id, rule.id): rule for audit_id, plan in plans.items() for rule in plan.rules}
    proofs = {(proof.audit_id, proof.rule_id): proof for proof in trace.proofs}
    if len(proofs) != len(trace.proofs) or set(proofs) != set(expected):
        raise ValueError("Every planned target/clause needs exactly one execution proof")
    bindings = {binding.id: binding for binding in trace.bindings}
    if len(bindings) != len(trace.bindings):
        raise ValueError("Duplicate binding identity")
    budget = EvidenceBudget()
    support_budget = EvidenceBudget()
    checks = []
    for identity, planned in expected.items():
        proof = proofs[identity]
        if isinstance(proof, QueryProof):
            if not isinstance(planned.predicate, QueryProgram):
                raise ValueError("Query proof contradicts the planned rule program")
            binding = bindings.get(proof.binding_id)
            if binding is None or binding.document_id not in documents or binding.document_id != proof.audit_id:
                raise ValueError("Missing query binding")
            document = documents[binding.document_id]
            status, total, checked, unknown, failures = query_counts(planned.predicate, binding, document, budget)
            evidence, diagnostic = _query_support(planned.predicate, binding, document, status, total, failures, support_budget)
            conclusion = {
                "PASS": "全部记录满足条件" if planned.predicate.quantifier == "forall" else "存在满足条件的记录",
                "NG": f"有 {len(failures)} 条记录不满足条件" if planned.predicate.quantifier == "forall" else "没有记录满足条件",
                "UNABLE": "范围内没有可检查记录" if not total else "存在无法确认条件的记录",
            }[status]
            addresses = "、".join(f"{binding.table}!{ref(row, column)}" for row, columns in list(failures.items())[:3] for column in sorted(columns)[:3])
            reason = f"{planned.description}：{conclusion}。共 {total} 条记录，已检查 {checked} 条，未知 {unknown} 条。"
            if status == "NG" and addresses:
                reason += f"不符合位置：{addresses}。"
        else:
            if not isinstance(planned.predicate, SemanticProgram):
                raise ValueError("Semantic proof contradicts the planned rule program")
            status, total, checked, unknown = semantic_summary(proof, planned)
            reason, evidence, diagnostic = _semantic_support(proof, planned, status, unknown)
        checks.append(Check(audit_id=proof.audit_id, rule_id=proof.rule_id, status=status, reason=reason, total=total, checked=checked, unknown=unknown, evidence=evidence, diagnostic=diagnostic))
    return AuditReport(checks=checks)


def query_witness(citation: Citation, binding: Binding, failures: dict[int, set[int]], budget: EvidenceBudget) -> bool:
    if citation.document_id != binding.document_id or citation.locator is None:
        return False
    locator = citation.locator.model_dump()
    if locator["kind"] == "docx_cell":
        return binding.table == f"table:{locator['table']}" and locator["column"] in failures.get(locator["row"], set())
    if locator.get("sheet") != binding.table:
        return False
    if locator["kind"] == "xlsx_cell":
        row = int(re.sub("[A-Z]", "", locator["cell"]))
        column = col_number(re.sub("[0-9]", "", locator["cell"]))
        return column in failures.get(row, set())
    if locator["kind"] == "xlsx_range":
        first, last = (int(re.sub("[A-Z]", "", locator[key])) for key in ("start", "end"))
        first_col, last_col = (col_number(re.sub("[0-9]", "", locator[key])) for key in ("start", "end"))
        budget.consume(sum(len(columns) for columns in failures.values()) + 1)
        return any(first <= row <= last and any(first_col <= column <= last_col for column in columns) for row, columns in failures.items())
    return False


def validate_trace(trace: AuditTrace, report: AuditReport, documents: dict[str, dict[str, Any]], plans: dict[str, RulePlan]) -> set[tuple[str, str]]:
    checks = {(check.audit_id, check.rule_id): check for check in report.checks}
    identities = [(proof.audit_id, proof.rule_id) for proof in trace.proofs]
    if len(set(identities)) != len(identities) or set(identities) != set(checks):
        raise ValueError("Every check needs exactly one execution proof")
    bindings = {binding.id: binding for binding in trace.bindings}
    if len(bindings) != len(trace.bindings):
        raise ValueError("Duplicate binding identity")
    budget = EvidenceBudget()
    citation_budget = EvidenceBudget()
    used: set[str] = set()
    evidence_gaps: set[tuple[str, str]] = set()
    for proof in trace.proofs:
        identity = (proof.audit_id, proof.rule_id)
        check = checks[proof.audit_id, proof.rule_id]
        planned = next((item for item in plans[proof.audit_id].rules if item.id == proof.rule_id), None)
        if planned is None:
            raise ValueError("Execution proof has no planned rule")
        if isinstance(proof, QueryProof):
            if not isinstance(planned.predicate, QueryProgram):
                raise ValueError("Query proof contradicts the planned rule program")
            binding = bindings.get(proof.binding_id)
            if binding is None or binding.document_id not in documents or binding.document_id != proof.audit_id:
                raise ValueError("Missing query binding")
            used.add(binding.id)
            status, total, checked, unknown, failures = query_counts(planned.predicate, binding, documents[binding.document_id], budget)
            if status == "NG" and planned.predicate.quantifier == "forall":
                try:
                    witness = citation_budget.remaining >= 0 and any(query_witness(citation, binding, failures, citation_budget) for citation in check.evidence)
                except ValueError:
                    witness = False
                if not witness:
                    evidence_gaps.add(identity)
        else:
            if not isinstance(planned.predicate, SemanticProgram):
                raise ValueError("Semantic proof contradicts the planned rule program")
            if not proof.scope_basis.strip() or any(not record.reason.strip() for record in proof.records):
                raise ValueError("Semantic proof lacks scope or reasoning")
            if len({record.record_id for record in proof.records}) != len(proof.records):
                raise ValueError("Duplicate semantic record identity")
            status, total, checked, unknown = semantic_summary(proof, planned)
            for record in proof.records:
                budget.consume(1)
                if record.status != "UNABLE" and not record.evidence:
                    evidence_gaps.add(identity)
                for citation in record.evidence:
                    if citation.document_id not in documents:
                        raise ValueError("Observation outside submission")
                    if resolve_optional(documents[citation.document_id], citation.locator, citation_budget) is None:
                        evidence_gaps.add(identity)
            if check.status != "UNABLE":
                empty_scope_result = not any(record.role == "applicable" for record in proof.records) and check.status == status and status in {"PASS", "NA"}
                # Excluded candidates establish the empty set. Their own NA
                # status must not override the rule's explicit PASS/NA policy.
                supporting = {citation.model_dump_json() for record in proof.records if record.status == check.status or empty_scope_result for citation in record.evidence}
                if not any(citation.model_dump_json() in supporting for citation in check.evidence):
                    evidence_gaps.add(identity)
        if (check.status, check.total, check.checked, check.unknown) != (status, total, checked, unknown):
            raise ValueError("Check contradicts replayed query or record observations")
    if used != set(bindings):
        raise ValueError("Unreferenced binding")
    return evidence_gaps
