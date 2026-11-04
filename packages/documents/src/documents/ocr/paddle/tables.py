"""Paddle HTML table grids and native PDF table fallback."""

from __future__ import annotations

import re
from contextlib import nullcontext
from html.parser import HTMLParser
from pathlib import Path

import fitz

from .client import PaddleOCRFormatError
from .layout import blocks, box, iou

MAX_TABLE_ROWS = 2000
MAX_TABLE_COLS = 256
MAX_TABLE_CELLS = 100_000
MAX_CELL_CHARS = 32767


class TableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.count = 0
        self.in_table = False
        self.row: list[tuple[str, int, int]] | None = None
        self.cell: tuple[int, int] | None = None
        self.parts: list[str] = []
        self.rows: list[list[tuple[str, int, int]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self.in_table or self.count:
                raise PaddleOCRFormatError("PaddleOCR table HTML must contain exactly one table")
            self.count += 1
            self.in_table = True
        elif self.in_table and tag == "tr":
            self._finish_row()
            self.row = []
        elif self.row is not None and tag in {"td", "th"}:
            self._finish_cell()
            if any(not re.fullmatch(r"[A-Za-z_:][-A-Za-z0-9_:.]*", name) for name, _ in attrs):
                raise PaddleOCRFormatError("PaddleOCR table HTML is incomplete")
            attrs_by_name = dict(attrs)
            spans = []
            for name, limit in (("rowspan", MAX_TABLE_ROWS), ("colspan", MAX_TABLE_COLS)):
                raw = attrs_by_name.get(name) or "1"
                try:
                    value = int(raw)
                except ValueError:
                    raise PaddleOCRFormatError("Invalid PaddleOCR table span") from None
                if value < 1 or value > limit:
                    raise PaddleOCRFormatError("PaddleOCR table span exceeds limits")
                spans.append(value)
            self.cell = spans[0], spans[1]
            self.parts = []
        elif tag == "br" and self.cell is not None:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.parts.append(data)

    def _finish_cell(self) -> None:
        if self.cell is None:
            return
        if self.row is None:
            raise PaddleOCRFormatError("PaddleOCR table cell is outside a row")
        text = "".join(self.parts).strip()
        if len(text) > MAX_CELL_CHARS:
            raise PaddleOCRFormatError("PaddleOCR cell exceeds Excel size limit")
        self.row.append((text, *self.cell))
        self.cell = None
        self.parts = []

    def _finish_row(self) -> None:
        self._finish_cell()
        if self.row is not None:
            self.rows.append(self.row)
            self.row = None
            if len(self.rows) > MAX_TABLE_ROWS:
                raise PaddleOCRFormatError("PaddleOCR table exceeds row limit")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._finish_cell()
        elif tag in {"tr", "tbody", "thead", "tfoot"}:
            self._finish_row()
        elif tag == "table" and self.in_table:
            # HTML permits omitted cell / row end tags here; the table itself
            # must still close. Do not invent missing cells or span coverage.
            self._finish_row()
            self.in_table = False

    def to_grid(self) -> tuple[list[list[str]], list[tuple[int, int, int, int]]]:
        if self.count != 1 or self.in_table or self.row is not None or self.cell is not None or not self.rows:
            raise PaddleOCRFormatError("PaddleOCR table HTML is incomplete")
        cells: dict[tuple[int, int], str] = {}
        merges = []
        width = 0
        for row_index, row in enumerate(self.rows):
            col = 0
            for text, rowspan, colspan in row:
                while (row_index, col) in cells:
                    col += 1
                if col + colspan > MAX_TABLE_COLS or row_index + rowspan > MAX_TABLE_ROWS:
                    raise PaddleOCRFormatError("PaddleOCR table exceeds dimension limits")
                for r in range(row_index, row_index + rowspan):
                    for c in range(col, col + colspan):
                        if (r, c) in cells:
                            raise PaddleOCRFormatError("PaddleOCR table spans overlap")
                        cells[(r, c)] = ""
                cells[(row_index, col)] = text
                if rowspan > 1 or colspan > 1:
                    merges.append((row_index + 1, col + 1, row_index + rowspan, col + colspan))
                col += colspan
                width = max(width, col)
        if not width or any(r >= len(self.rows) for r, _ in cells) or width * len(self.rows) > MAX_TABLE_CELLS:
            raise PaddleOCRFormatError("PaddleOCR table dimensions are invalid")
        return [[cells.get((r, c), "") for c in range(width)] for r in range(len(self.rows))], merges


def parse_table(markup: str) -> tuple[list[list[str]], list[tuple[int, int, int, int]]]:
    if not markup or len(markup) > 2_000_000:
        raise PaddleOCRFormatError("PaddleOCR table HTML is missing or too large")
    parser = TableHTML()
    parser.feed(markup)
    parser.close()
    return parser.to_grid()


def _candidate_bbox(table: dict) -> list[float] | None:
    coords = table.get("cell_box_list")
    if not coords:
        return None
    if not isinstance(coords, list):
        raise PaddleOCRFormatError("PaddleOCR table cell boxes are invalid")
    values = [box(item) for item in coords]
    coords_nonempty = [item for item in values if item is not None]
    if not coords_nonempty:
        return None
    return [min(c[0] for c in coords_nonempty), min(c[1] for c in coords_nonempty), max(c[2] for c in coords_nonempty), max(c[3] for c in coords_nonempty)]


def _native_rows(page: fitz.Page, bounds: list[float] | None, image_size: tuple[float, float]) -> list[list[str]] | None:
    if bounds is None or page.rotation:
        # Rotated source regions require an explicit rotation-aware extraction;
        # keep Paddle's page geometry instead of guessing a native crop.
        return None
    iw, ih = image_size
    if iw <= 0 or ih <= 0:
        raise PaddleOCRFormatError("PaddleOCR image dimensions are invalid")
    clip = fitz.Rect(bounds[0] * page.rect.width / iw, bounds[1] * page.rect.height / ih, bounds[2] * page.rect.width / iw, bounds[3] * page.rect.height / ih)
    clip &= page.rect
    if clip.is_empty:
        return None
    try:
        found = page.find_tables(clip=clip)
        tables = getattr(found, "tables", [])
        if len(tables) != 1:
            return None
        extracted = tables[0].extract()
    except (AttributeError, ValueError, RuntimeError):
        return None
    if not isinstance(extracted, list) or not extracted:
        return None
    rows = []
    for row in extracted:
        if not isinstance(row, list) or not row:
            return None
        values = ["" if cell is None else str(cell) for cell in row]
        if any(len(cell) > MAX_CELL_CHARS for cell in values):
            raise PaddleOCRFormatError("Native table cell exceeds Excel size limit")
        rows.append(values)
    if len(rows) > MAX_TABLE_ROWS or max(map(len, rows)) > MAX_TABLE_COLS or len(rows) * max(map(len, rows)) > MAX_TABLE_CELLS:
        raise PaddleOCRFormatError("Native table exceeds dimension limits")
    if len({len(row) for row in rows}) != 1 or not any(cell.strip() for row in rows for cell in row):
        return None
    return rows


def _bind_recognitions(regions: list[dict], recognized: list[dict]) -> tuple[dict[int, int], set[int]]:
    assignments: dict[int, int] = {}
    consumed: set[int] = set()
    if len(regions) == 1 and len(recognized) == 1:
        left, right = regions[0]["bbox"], recognized[0]["bbox"]
        if left is None or right is None or iou(left, right) > 0:
            assignments[regions[0]["index"]] = 0
            return assignments, {0}
    candidates = []
    for region in regions:
        for i, item in enumerate(recognized):
            if region["bbox"] is not None and item["bbox"] is not None:
                score = iou(region["bbox"], item["bbox"])
                if score > 0:
                    candidates.append((score, region["index"], i))
    candidates.sort(reverse=True)
    for score, region_id, index in candidates:
        if region_id in assignments or index in consumed:
            continue
        # A best match must be unique from both directions; tied regions must
        # not acquire another region's recognized text based on list order.
        region_scores = [x[0] for x in candidates if x[1] == region_id]
        recognition_scores = [x[0] for x in candidates if x[2] == index]
        if score != max(region_scores) or score != max(recognition_scores):
            continue
        if sum(abs(value - score) < 1e-9 for value in region_scores) == 1 and sum(abs(value - score) < 1e-9 for value in recognition_scores) == 1:
            assignments[region_id] = index
            consumed.add(index)
    return assignments, consumed


def recognize_tables(
    pdf_path: str | Path | None,
    result: dict,
    layouts: list[dict],
    *,
    diagnostics: list[dict] | None = None,
    source_pages: list[int] | None = None,
) -> list[dict]:
    """Extract independent tables; diagnostics isolate failures when requested."""
    tables: list[dict] = []
    page_sizes = result.get("dataInfo", {}).get("pages", [])

    def failed(error: PaddleOCRFormatError, page: int, region: int | None, recognition: int | None) -> None:
        if diagnostics is None:
            raise error
        diagnostics.append({"code": "paddle.table.invalid", "stage": "structure", "element": "table", "message": str(error), "page": page, "region_index": region, "recognition_index": recognition})

    with fitz.open(pdf_path) if pdf_path is not None else nullcontext(None) as pdf:
        for page_index, entry in enumerate(layouts):
            page_no = source_pages[page_index] if source_pages is not None else page_index + 1
            pruned = result["layoutParsingResults"][page_index]["prunedResult"]
            try:
                raw_tables = blocks(pruned, "table_res_list")
            except PaddleOCRFormatError as error:
                failed(error, page_no, None, None)
                continue
            recognized = []
            for index, item in enumerate(raw_tables):
                try:
                    if not isinstance(item, dict) or not isinstance(item.get("pred_html"), str):
                        raise PaddleOCRFormatError("PaddleOCR table is missing pred_html")
                    recognized.append({"index": index, "bbox": _candidate_bbox(item), "html": item["pred_html"]})
                except PaddleOCRFormatError as error:
                    failed(error, page_no, None, index)
            regions = [region for region in entry["regions"] if region["label"] == "table"]
            match, consumed = _bind_recognitions(regions, recognized)
            dims = page_sizes[page_index] if isinstance(page_sizes, list) and len(page_sizes) > page_index else {}
            if not isinstance(dims, dict):
                failed(PaddleOCRFormatError("PaddleOCR page metadata is invalid"), page_no, None, None)
                continue
            width, height = dims.get("width"), dims.get("height")
            size = (float(width), float(height)) if isinstance(width, (int, float)) and not isinstance(width, bool) and isinstance(height, (int, float)) and not isinstance(height, bool) else None
            for region in regions:
                target = match.get(region["index"])
                recognition_index = recognized[target]["index"] if target is not None else None
                try:
                    native = _native_rows(pdf[page_no - 1], region["bbox"], size) if pdf is not None and size is not None else None
                    if native is not None:
                        rows = native
                        merges: list[tuple[int, int, int, int]] = []
                        source = "native_pdf"
                    elif target is not None:
                        rows, merges = parse_table(recognized[target]["html"])
                        source = "paddle_html"
                    else:
                        raise PaddleOCRFormatError("PaddleOCR detected a table but omitted its recognition")
                    tables.append({"page": page_no, "region_index": region["index"], "recognition_index": recognition_index, "bbox": region["bbox"], "source": source, "rows": rows, "merges": merges})
                except PaddleOCRFormatError as error:
                    failed(error, page_no, region["index"], recognition_index)
            for index, item in enumerate(recognized):
                if index in consumed:
                    continue
                try:
                    if regions and item["bbox"] is None:
                        raise PaddleOCRFormatError("PaddleOCR table cannot be spatially matched")
                    if regions and item["bbox"] is not None and any(region["bbox"] is not None and iou(region["bbox"], item["bbox"]) > 0 for region in regions):
                        raise PaddleOCRFormatError("PaddleOCR table geometry is ambiguous")
                    rows, merges = parse_table(item["html"])
                    tables.append({"page": page_no, "region_index": None, "recognition_index": item["index"], "bbox": item["bbox"], "source": "paddle_html_unlocated", "rows": rows, "merges": merges})
                except PaddleOCRFormatError as error:
                    failed(error, page_no, None, item["index"])
    return tables
