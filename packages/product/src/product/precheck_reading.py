"""Deterministic text and table reading for the content-level precheck: no OCR, no models.

Every carrier becomes the same shape: grids of cell text (worksheets, Word tables, PDF tables) and
located text lines (Word paragraphs, headers, footers, PDF page text). Merged cells repeat their value
over the whole span so headers and vertically merged keys read the same on every covered cell.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from typing import Any

from openpyxl.utils import get_column_letter

MAX_ROWS, MAX_COLS = 3000, 120
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic"}


@dataclass
class Grid:
    """One table: a worksheet, a Word table or a PDF table."""

    name: str
    rows: list[list[str]]
    kind: str  # xlsx | docx | pdf
    images: set[tuple[int, int]] = field(default_factory=set)  # 0-based anchor cells of embedded pictures (xlsx only)

    def cell(self, r: int, c: int) -> str:
        return self.rows[r][c] if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[r]) else ""

    def ref(self, r: int, c: int | None = None) -> str:
        if self.kind == "xlsx":
            return f"{self.name}!{get_column_letter(c + 1)}{r + 1}" if c is not None else f"{self.name} 第 {r + 1} 行"
        return f"{self.name} 第 {r + 1} 行" + (f"第 {c + 1} 列" if c is not None else "")


@dataclass
class Doc:
    kind: str  # xlsx | docx | pdf | image | unsupported
    grids: list[Grid] = field(default_factory=list)
    lines: list[tuple[str, str]] = field(default_factory=list)  # (location, text)
    images_known: bool = False  # True only when embedded pictures were enumerated (xlsx)
    error: str = ""

    @property
    def has_text(self) -> bool:
        return any(text.strip() for grid in self.grids for row in grid.rows for text in row) or any(text.strip() for _, text in self.lines)


@lru_cache(maxsize=1 << 17)
def norm(text: Any) -> str:
    """Compare form: NFKC, no whitespace (incl. full-width and NBSP), lower case."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).lower()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time() else value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _xlsx(content: bytes) -> Doc:
    from documents.excel.loader import ExcelLoader

    head = content[:4]
    workbook = ExcelLoader.load_workbook(BytesIO(content))
    doc = Doc("xlsx", images_known=head == ExcelLoader.ZIP_MAGIC)
    try:
        for ws in workbook.worksheets:
            height, width = min(ws.max_row or 0, MAX_ROWS), min(ws.max_column or 0, MAX_COLS)
            rows = [[_text(value) for value in row] for row in ws.iter_rows(min_row=1, max_row=height, max_col=width, values_only=True)] if height and width else []
            for merged in ws.merged_cells.ranges:
                top = rows[merged.min_row - 1][merged.min_col - 1] if merged.min_row <= height and merged.min_col <= width else ""
                for r in range(merged.min_row - 1, min(merged.max_row, height)):
                    for c in range(merged.min_col - 1, min(merged.max_col, width)):
                        rows[r][c] = top
            images = set()
            for image in getattr(ws, "_images", []):
                anchor = getattr(getattr(image, "anchor", None), "_from", None)
                if anchor is not None:
                    images.add((int(anchor.row), int(anchor.col)))
            doc.grids.append(Grid(ws.title, rows, "xlsx", images))
    finally:
        workbook.close()
    return doc


def _word_grid(name: str, table: Any) -> Grid:
    rows = [["" for _ in range(table.grid_column_count)] for _ in range(table.row_count)]
    for cell in table.cells:
        for r in range(cell.row, min(cell.row + max(cell.rowspan, 1), table.row_count)):
            for c in range(cell.col, min(cell.col + max(cell.colspan, 1), table.grid_column_count)):
                rows[r][c] = cell.text
    return Grid(name, rows, "docx")


def _docx(content: bytes) -> Doc:
    from documents.word.parser import extract_docx_structure

    parsed = extract_docx_structure(content)
    doc = Doc("docx")
    count = 0
    for region, blocks in (("正文", parsed.blocks), ("页眉", parsed.headers), ("页脚", parsed.footers)):
        for index, block in enumerate(blocks, 1):
            if block.block_type == "table":
                count += 1
                doc.grids.append(_word_grid(f"表格 {count}", block))
            elif block.text.strip():
                doc.lines.append((f"{region}第 {index} 段", block.text))
    if not parsed.valid and not doc.has_text:
        doc.error = "Word 文件结构无法解析"
    return doc


def _pdf(content: bytes) -> Doc:
    import pymupdf

    doc = Doc("pdf")
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        for number, page in enumerate(pdf, 1):
            for line in page.get_text("text").splitlines():
                if line.strip():
                    doc.lines.append((f"第 {number} 页", line))
            try:
                tables = page.find_tables().tables
            except Exception:  # table detection is best effort; page text above stays available
                tables = []
            for index, table in enumerate(tables, 1):
                rows = [[_text(cell).replace("\n", " ") for cell in row] for row in table.extract()]
                doc.grids.append(Grid(f"第 {number} 页 表 {index}", rows, "pdf"))
    return doc


def read_document(content: bytes, filename: str) -> Doc:
    """Read one file's text and tables. Unknown or unreadable carriers come back with ``error`` set."""
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    try:
        if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}:
            return _xlsx(content)
        if suffix == ".docx":
            return _docx(content)
        if suffix == ".pdf":
            return _pdf(content)
    except Exception as exc:  # unreadable content is reported, never guessed
        return Doc("unsupported", error=f"文件无法读取（{type(exc).__name__}）")
    if suffix in IMAGE_SUFFIXES:
        return Doc("image")
    return Doc("unsupported", error=f"预检不读取 {suffix or '无扩展名'} 格式的文字")
