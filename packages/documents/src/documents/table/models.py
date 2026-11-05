"""Core table data models shared across PDF and Excel layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CellRegion:
    """Table cell region, supports merged spans."""

    row_start: int
    row_end: int
    col_start: int
    col_end: int
    content: str = ""
    is_unknown: bool = False
    source_page: int | None = None
    source_bbox: tuple[float, float, float, float] | None = None
    source_value: str | None = None

    def __str__(self) -> str:
        return f"Cell({self.row_start}-{self.row_end}, {self.col_start}-{self.col_end}): {self.content}"


@dataclass
class TableGrid:
    """Logical table grid produced by line reconstruction."""

    rows: list[float]
    cols: list[float]
    cells: list[CellRegion]


__all__ = ["CellRegion", "TableGrid"]
