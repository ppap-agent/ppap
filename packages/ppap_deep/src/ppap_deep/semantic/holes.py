"""Typed local evidence requests. Answers remain evidence, never aggregate verdicts."""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import Field

from .document_tools import col_number
from .evidence import EvidenceBudget, read_cell, resolve
from .models import Artifact, Citation, ExcelCell, ExcelRange, PdfCell, WordCell


class EvidenceHole(Artifact):
    id: str
    input_id: str = ""
    audit_id: str
    rule_id: str
    kind: Literal["semantic_predicate", "semantic_key", "semantic_value", "attribute", "object_scope", "relationship", "interpretation"]
    program_path: str
    question: str
    context: dict[str, Any]
    value_type: Literal["boolean", "string", "number", "objects", "relationship", "interpretation"] = "boolean"


class TypedAnswer(Artifact):
    kind: Literal["fact"]
    need_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    value: Any = None
    state: Literal["provided", "unknown", "none_of_candidates"] = "provided"
    origin: Literal["native", "model"]
    reason: str = Field(min_length=1, max_length=4096)
    evidence: list[Citation] = Field(default_factory=list, max_length=64)


def _inside_object(source: Citation, scope: dict[str, Any]) -> bool:
    if source.document_id != scope["document_id"]:
        return False
    locator = source.locator

    def allowed(row: int) -> bool:
        return row in scope["rows"] if "rows" in scope else scope["first_row"] <= row <= scope["last_row"] or row in scope.get("header_rows", [])

    if isinstance(locator, (ExcelCell, ExcelRange)):
        table = locator.sheet
        start = locator.cell if isinstance(locator, ExcelCell) else locator.start
        end = locator.cell if isinstance(locator, ExcelCell) else locator.end
        first, last = int(re.sub("[A-Z]", "", start)), int(re.sub("[A-Z]", "", end))
        return table == scope["table"] and first <= last and last - first < 4096 and all(allowed(row) for row in range(first, last + 1))
    if isinstance(locator, (WordCell, PdfCell)):
        table = f"table:{locator.table}" if isinstance(locator, WordCell) else f"page-{locator.page}"
        return table == scope["table"] and allowed(locator.row)
    return False


def validate_answer(hole: EvidenceHole, answer: TypedAnswer, documents: dict[str, dict[str, Any]], *, budget: EvidenceBudget | None = None) -> Any:
    """Validate only the requested fact and its frozen local context."""
    if hole.id != answer.need_id or hole.input_id != answer.input_id:
        raise ValueError("Typed answer belongs to another input or request")
    if answer.state != "provided":
        if answer.value is not None:
            raise ValueError("Unanswered facts cannot carry a value")
        return None
    if not answer.reason.strip() or not answer.evidence:
        raise ValueError("A provided fact requires source evidence and a reason")
    allowed = set(hole.context.get("document_ids", documents.keys()))
    observations: list[Any] = []
    for source in answer.evidence:
        if source.document_id not in documents or source.document_id not in allowed or source.locator is None:
            raise ValueError("Typed fact evidence outside requested documents")
        if "source_scope" in hole.context and not _inside_object(source, hole.context["source_scope"]):
            raise ValueError("Typed fact evidence outside requested object")
        if "allowed_evidence" in hole.context and source.model_dump(mode="json") not in hole.context["allowed_evidence"]:
            raise ValueError("Typed fact evidence outside requested source locations")
        observed = resolve(documents[source.document_id], source.locator, budget or EvidenceBudget())
        if answer.origin == "native" and not observed.known:
            raise ValueError("A native fact requires a known native observation")
        if isinstance(source.locator, ExcelRange):
            range_locator = source.locator
            table = next(table for table in documents[source.document_id]["tables"] if table["id"] == range_locator.sheet)
            row1, row2 = (int(re.sub("[A-Z]", "", address)) for address in (range_locator.start, range_locator.end))
            column1, column2 = (col_number(re.sub("[0-9]", "", address)) for address in (range_locator.start, range_locator.end))
            observations.extend(read_cell(table, row, column)["value"] for row in range(row1, row2 + 1) for column in range(column1, column2 + 1))
        elif isinstance(source.locator, (ExcelCell, WordCell, PdfCell)):
            locator = source.locator
            if isinstance(locator, ExcelCell):
                table_id = locator.sheet
                row = int(re.sub("[A-Z]", "", locator.cell))
                column = col_number(re.sub("[0-9]", "", locator.cell))
            else:
                table_id = f"table:{locator.table}" if isinstance(locator, WordCell) else f"page-{locator.page}"
                row, column = locator.row, locator.column
            table = next(table for table in documents[source.document_id]["tables"] if table["id"] == table_id)
            observations.append(read_cell(table, row, column)["value"])
        else:
            observations.extend(observed.values)
    value = answer.value
    kind = hole.value_type
    if answer.origin == "native" and (kind in {"objects", "relationship", "interpretation"} or hole.kind in {"semantic_predicate", "semantic_key", "semantic_value"}):
        raise ValueError("A structural interpretation is not a native scalar observation")
    if kind == "boolean":
        valid = type(value) is bool
    elif kind == "number":
        valid = type(value) is int or type(value) is float and math.isfinite(value)
    elif kind in {"string", "interpretation"}:
        valid = isinstance(value, str) and 0 < len(value) <= 4096
        if kind == "interpretation":
            valid = valid and value in hole.context.get("candidate_ids", [])
    else:
        known = hole.context.get("objects", [])
        allowed_refs = {(ref["binding_id"], ref["object_id"]) for ref in known}

        def reference(item: Any) -> bool:
            return isinstance(item, dict) and set(item) == {"binding_id", "object_id"} and (item["binding_id"], item["object_id"]) in allowed_refs

        if kind == "objects" and "object_choices" in hole.context:
            valid = isinstance(value, str) and value in {choice["id"] for choice in hole.context["object_choices"]}
        elif kind == "objects":
            valid = isinstance(value, list) and 0 < len(value) <= 4096 and all(reference(ref) for ref in value)
        else:
            valid = (
                isinstance(value, dict)
                and set(value) in ({"source", "target"}, {"source", "target", "exists"})
                and type(value.get("exists", True)) is bool
                and reference(value["source"])
                and reference(value["target"])
                and ("relation" not in hole.context or value["source"] == hole.context["relation"]["source"] and value["target"] == hole.context["relation"]["target"])
            )
    if not valid:
        raise ValueError("Typed fact does not match the requested type or object scope")
    if answer.origin == "native" and kind in {"boolean", "number", "string"} and not any(type(item) is type(value) and item == value for item in observations):
        raise ValueError("Native fact differs from the original value")
    return value
