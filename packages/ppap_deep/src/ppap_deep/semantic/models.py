"""Validated artifacts; these are internal and do not extend the public API."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExcelCell(Artifact):
    kind: Literal["xlsx_cell"]
    sheet: str = Field(min_length=1)
    cell: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


class ExcelRange(Artifact):
    kind: Literal["xlsx_range"]
    sheet: str = Field(min_length=1)
    start: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
    end: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


class WordParagraph(Artifact):
    kind: Literal["docx_paragraph"]
    paragraph: int = Field(ge=1, strict=True)


class WordCell(Artifact):
    kind: Literal["docx_cell"]
    table: int = Field(ge=1, strict=True)
    row: int = Field(ge=1, strict=True)
    column: int = Field(ge=1, strict=True)


class PdfBlock(Artifact):
    kind: Literal["pdf_block"]
    page: int = Field(ge=1, strict=True)
    block: int = Field(ge=1, strict=True)


class PublishedClause(Artifact):
    """An effective published rule clause, never a coordinate in the original file."""

    kind: Literal["published_clause"]
    clause_id: str = Field(min_length=1, max_length=256)


Locator = Annotated[ExcelCell | ExcelRange | WordParagraph | WordCell | PdfBlock | PublishedClause, Field(discriminator="kind")]
Scalar: TypeAlias = str | int | float | bool | None


class Predicate(Artifact):
    field: str
    op: Literal["not_empty", "empty", "eq", "ne", "in", "gt", "ge", "lt", "le"]
    value: Scalar = None
    values: list[Scalar] = Field(default_factory=list, max_length=256)


class QueryProgram(Artifact):
    kind: Literal["query"]
    predicates: list[Predicate] = Field(min_length=1, max_length=32)
    quantifier: Literal["forall", "exists"]


class SemanticProgram(Artifact):
    kind: Literal["semantic"]
    question: str = Field(min_length=1)


RuleProgram = Annotated[QueryProgram | SemanticProgram, Field(discriminator="kind")]


class PlannedRule(Artifact):
    id: str
    description: str
    priority: str = "P1"
    source_locator: Locator
    scope: str
    predicate: RuleProgram
    missing_evidence: str
    ambiguities: list[str]
    # Required even for UNABLE so old cached plans cannot silently inherit a
    # new empty-scope policy. PASS/NA require an explicit source-based basis.
    empty_scope_status: Literal["UNABLE", "NA", "PASS"]
    empty_scope_basis: str = Field(min_length=1)

    @model_validator(mode="after")
    def explained_empty_scope(self) -> PlannedRule:
        if not self.empty_scope_basis.strip():
            raise ValueError("Empty-scope policy needs an explanation")
        return self


class RulePlan(Artifact):
    source_sha256: str
    plan_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    rules: list[PlannedRule] = Field(min_length=1)


class Citation(Artifact):
    document_id: str
    locator: Locator | None = None


class Check(Artifact):
    audit_id: str
    rule_id: str
    status: Literal["PASS", "NG", "UNABLE", "NA"]
    reason: str
    total: int = Field(ge=0, strict=True)
    checked: int = Field(ge=0, strict=True)
    unknown: int = Field(ge=0, strict=True)
    evidence: list[Citation] = Field(default_factory=list, max_length=64)
    diagnostic: Literal["parser_unknown", "unresolved_relation", "visual_only_evidence", "budget_exhausted", "unsupported_structure", "rule_ambiguity"] | None = None


class AuditReport(Artifact):
    checks: list[Check] = Field(min_length=1, max_length=16384)
