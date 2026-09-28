"""Bounded, replayable table predicates and per-record semantic observations."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .document_tools import col_number, effective
from .evidence import EvidenceBudget, resolve
from .models import Artifact, AuditReport, Citation, ExcelCell, PlannedRule, Predicate, QueryProgram, RulePlan, Scalar, SemanticProgram, WordCell

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
    evidence: list[Citation] = Field(max_length=64)

    @model_validator(mode="after")
    def explained_role(self) -> Observation:
        if self.role == "applicable" and (
            not (self.applicability_basis or "").strip() or self.exclusion_basis is not None or not self.evidence or self.status == "NA"
        ):
            raise ValueError("Applicable record needs its rule-trigger basis and source evidence")
        if self.role == "excluded" and (
            not (self.exclusion_basis or "").strip() or self.applicability_basis is not None or not self.evidence or self.status not in {"NA", "UNABLE"}
        ):
            raise ValueError("Excluded record needs its non-applicability basis and source evidence")
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
    proofs: list[Proof] = Field(min_length=1, max_length=16384)


class AuditExecution(Artifact):
    report: AuditReport
    trace: AuditTrace


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


def query_counts(program: QueryProgram, binding: Binding, document: dict[str, Any], budget: EvidenceBudget) -> tuple[str, int, int, int, dict[int, set[int]]]:
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


def query_witness(citation: Citation, binding: Binding, failures: dict[int, set[int]], budget: EvidenceBudget) -> bool:
    if citation.document_id != binding.document_id:
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


def validate_trace(trace: AuditTrace, report: AuditReport, documents: dict[str, dict[str, Any]], plans: dict[str, RulePlan]) -> None:
    checks = {(check.audit_id, check.rule_id): check for check in report.checks}
    identities = [(proof.audit_id, proof.rule_id) for proof in trace.proofs]
    if len(set(identities)) != len(identities) or set(identities) != set(checks):
        raise ValueError("Every check needs exactly one execution proof")
    bindings = {binding.id: binding for binding in trace.bindings}
    if len(bindings) != len(trace.bindings):
        raise ValueError("Duplicate binding identity")
    budget = EvidenceBudget()
    used: set[str] = set()
    for proof in trace.proofs:
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
                if not any(query_witness(citation, binding, failures, budget) for citation in check.evidence):
                    raise ValueError("NG evidence must identify a replayed counterexample")
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
                    raise ValueError("Semantic observation lacks evidence")
                for citation in record.evidence:
                    if citation.document_id not in documents:
                        raise ValueError("Observation outside submission")
                    observed = resolve(documents[citation.document_id], citation.locator, budget)
                    if record.status != "UNABLE" and not observed.known:
                        raise ValueError("Semantic observation uses unknown evidence")
            if check.status != "UNABLE":
                empty_scope_result = (
                    not any(record.role == "applicable" for record in proof.records)
                    and check.status == status and status in {"PASS", "NA"}
                )
                # Excluded candidates establish the empty set. Their own NA
                # status must not override the rule's explicit PASS/NA policy.
                supporting = {citation.model_dump_json() for record in proof.records
                              if record.status == check.status or empty_scope_result
                              for citation in record.evidence}
                if not any(citation.model_dump_json() in supporting for citation in check.evidence):
                    raise ValueError("Conclusion evidence does not support its semantic status")
        if (check.status, check.total, check.checked, check.unknown) != (status, total, checked, unknown):
            raise ValueError("Check contradicts replayed query or record observations")
    if used != set(bindings):
        raise ValueError("Unreferenced binding")
