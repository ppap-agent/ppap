"""Result models for PDF table extraction and conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..contracts.geometry import LineLike
from ..extractors.pdf import SpanBox
from ..table.models import TableGrid
from .spec import PdfSpec, StitchMode

PdfSource = str | Path | bytes
TableRegion = tuple[float | None, float, float | None, float]


@dataclass(frozen=True)
class OuterTextLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    position: Literal["header", "footer"]


@dataclass(frozen=True)
class PageExtraction:
    page_index: int
    success: bool
    table_grid: TableGrid
    span_boxes: list[SpanBox]
    line_elements: list[LineLike]
    intersections_count: int
    cells_count: int
    page_height: float | None = None
    outer_text_lines: list[OuterTextLine] = field(default_factory=list)
    table_region: TableRegion | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class GroupExtraction:
    group_index: int
    pages: tuple[int, ...]
    strategy: StitchMode
    success: bool
    page_results: list[PageExtraction]
    table_grid: TableGrid | None
    cells_count: int
    intersections_count: int
    error_message: str | None = None
    sheet_name: str | None = None
    group_label: str | None = None
    group_diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    spec: PdfSpec
    groups: list[GroupExtraction]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class ConversionResult:
    output_path: str
    extraction: ExtractionResult
    diagnostics: dict[str, Any]


__all__ = [
    "ConversionResult",
    "ExtractionResult",
    "GroupExtraction",
    "OuterTextLine",
    "PageExtraction",
    "PdfSource",
    "TableRegion",
]
