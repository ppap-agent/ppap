"""Shared observation types, scalar predicates and source locations."""

from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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


class PdfCell(Artifact):
    kind: Literal["pdf_cell"]
    page: int = Field(ge=1, strict=True)
    row: int = Field(ge=1, strict=True)
    column: int = Field(ge=1, strict=True)


class PublishedClause(Artifact):
    """An effective published rule clause, never a coordinate in the original file."""

    kind: Literal["published_clause"]
    clause_id: str = Field(min_length=1, max_length=256)


Locator = Annotated[ExcelCell | ExcelRange | WordParagraph | WordCell | PdfBlock | PdfCell | PublishedClause, Field(discriminator="kind")]

_PUBLIC_LOCATORS: dict[str, type[Artifact]] = {
    "xlsx_cell": ExcelCell,
    "xlsx_range": ExcelRange,
    "docx_paragraph": WordParagraph,
    "docx_cell": WordCell,
    "pdf_block": PdfBlock,
    "pdf_cell": PdfCell,
}


def _finite_coordinate(value: object) -> bool:
    if value is None:
        return True
    if type(value) is bool or type(value) not in {int, float}:
        return False
    return type(value) is int or math.isfinite(cast(float, value))


def _pdf_page(value: dict) -> dict | None:
    page = value.get("page")
    if type(page) is not int or page < 1:
        return None
    allowed = {"kind", "page"}
    parsed: dict = {"kind": "pdf_page", "page": page}
    if "region" in value:
        region = value.get("region")
        if not isinstance(region, list) or len(region) != 4 or any(not _finite_coordinate(item) for item in region):
            return None
        parsed["region"] = list(region)
        allowed.add("region")
    if set(value) != allowed:
        return None
    return parsed


def public_locator(value: object) -> dict | None:
    """Return one publishable original location, or nothing when it cannot be resolved."""
    if not isinstance(value, dict):
        return None
    if value.get("kind") == "pdf_page":
        return _pdf_page(value)
    kind = value.get("kind")
    model = _PUBLIC_LOCATORS.get(kind) if isinstance(kind, str) else None
    if model is None:
        return None
    try:
        return model.model_validate(value).model_dump()
    except ValidationError:
        return None


Scalar: TypeAlias = str | int | float | bool | None
Diagnostic: TypeAlias = Literal["parser_unknown", "unresolved_relation", "visual_only_evidence", "budget_exhausted", "unsupported_structure", "rule_ambiguity"]


class Predicate(Artifact):
    field: str
    op: Literal["not_empty", "empty", "eq", "ne", "in", "gt", "ge", "lt", "le"]
    value: Scalar = None
    values: list[Scalar] = Field(default_factory=list, max_length=256)


class InterpretationCandidate(Artifact):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=1024)
    explanation: str = Field(min_length=1, max_length=4096)


class Citation(Artifact):
    document_id: str
    locator: Locator | None = None
