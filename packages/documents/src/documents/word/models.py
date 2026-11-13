"""Stable structured Word document contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class WordSource:
    """Traceable location of one OOXML structure."""

    part: str
    region: Literal["body", "header", "footer", "textbox", "cell"]
    path: str


@dataclass(frozen=True)
class WordDiagnostic:
    """Explicit parser diagnostic; errors make the document invalid."""

    code: str
    message: str
    source: WordSource
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class WordVisualEvidence:
    """Traceable non-text Word evidence without embedding binary payloads."""

    source: WordSource
    kind: Literal["checkbox_character", "content_control", "embedded_image", "drawing"]
    checked: bool | None = None
    label: str = ""
    relationship_id: str | None = None
    target_part: str | None = None


@dataclass(frozen=True)
class WordImageEvidence:
    """Resolved image payload paired with its stable Word evidence record."""

    evidence: WordVisualEvidence
    data_url: str


@dataclass(frozen=True)
class WordParagraphBlock:
    source: WordSource
    text: str
    block_type: Literal["paragraph"] = field(default="paragraph", init=False)


@dataclass(frozen=True)
class WordTableCell:
    source: WordSource
    row: int
    col: int
    rowspan: int
    colspan: int
    text: str
    paragraphs: tuple[str, ...]
    blocks: tuple[WordBlock, ...]
    origin: bool = True


@dataclass(frozen=True)
class WordTableBlock:
    source: WordSource
    grid_column_count: int
    row_count: int
    cells: tuple[WordTableCell, ...]
    valid: bool
    diagnostics: tuple[WordDiagnostic, ...] = ()
    block_type: Literal["table"] = field(default="table", init=False)


WordBlock = WordParagraphBlock | WordTableBlock


@dataclass(frozen=True)
class WordDocument:
    """Serializable structured output for one DOCX package."""

    valid: bool
    blocks: tuple[WordBlock, ...]
    headers: tuple[WordBlock, ...] = ()
    footers: tuple[WordBlock, ...] = ()
    visual_evidence: tuple[WordVisualEvidence, ...] = ()
    diagnostics: tuple[WordDiagnostic, ...] = ()
    schema_version: Literal["1.0"] = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
