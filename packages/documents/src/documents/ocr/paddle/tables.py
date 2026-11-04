"""Paddle HTML tables, native PDF crop fallback and conservative page binding."""

from __future__ import annotations

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
            if self.row is not None:
                raise PaddleOCRFormatError("Nested table rows are invalid")
            self.row = []
        elif self.row is not None and tag in {"td", "th"}:
            if self.cell is not None:
                raise PaddleOCRFormatError("Nested table cells are invalid")
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

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None:
            if self.row is None:
                raise PaddleOCRFormatError("PaddleOCR table cell is outside a row")
            text = "".join(self.parts).strip()
            if len(text) > MAX_CELL_CHARS:
                raise PaddleOCRFormatError("PaddleOCR cell exceeds Excel size limit")
            self.row.append((text, *self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.cell is not None:
                raise PaddleOCRFormatError("PaddleOCR table contains unfinished cells")
            self.rows.append(self.row)
            self.row = None
            if len(self.rows) > MAX_TABLE_ROWS:
                raise PaddleOCRFormatError("PaddleOCR table exceeds row limit")
        elif tag == "table" and self.in_table:
            if self.row is not None or self.cell is not None:
                raise PaddleOCRFormatError("PaddleOCR table HTML is incomplete")
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
    if bounds is None:
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
    if len({len(row) for row in rows}) != 1:
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
        if region_id not in assignments and index not in consumed:
            equally_best = [x for x in candidates if x[1] == region_id and abs(x[0] - score) < 1e-9]
            if len(equally_best) == 1:
                assignments[region_id] = index
                consumed.add(index)
    return assignments, consumed


def recognize_tables(pdf_path: str | Path | None, result: dict, layouts: list[dict]) -> list[dict]:
    tables: list[dict] = []
    page_sizes = result.get("dataInfo", {}).get("pages", [])
    with fitz.open(pdf_path) if pdf_path is not None else nullcontext(None) as pdf:
        for page_index, entry in enumerate(layouts):
            pruned = result["layoutParsingResults"][page_index]["prunedResult"]
            raw_tables = blocks(pruned, "table_res_list")
            recognized = []
            for index, item in enumerate(raw_tables):
                if not isinstance(item, dict) or not isinstance(item.get("pred_html"), str):
                    raise PaddleOCRFormatError("PaddleOCR table is missing pred_html")
                recognized.append({"index": index, "bbox": _candidate_bbox(item), "html": item["pred_html"]})
            regions = [region for region in entry["regions"] if region["label"] == "table"]
            match, consumed = _bind_recognitions(regions, recognized)
            dims = page_sizes[page_index] if isinstance(page_sizes, list) and len(page_sizes) > page_index else {}
            if not isinstance(dims, dict):
                raise PaddleOCRFormatError("PaddleOCR page metadata is invalid")
            width, height = float(dims.get("width") or 1654), float(dims.get("height") or 2339)
            for region in regions:
                native = _native_rows(pdf[page_index], region["bbox"], (width, height)) if pdf is not None else None
                target = match.get(region["index"])
                if native is not None:
                    rows, merges, source = native, [], "native_pdf"
                elif target is not None:
                    rows, merges = parse_table(recognized[target]["html"])
                    source = "paddle_html"
                else:
                    raise PaddleOCRFormatError("PaddleOCR detected a table but omitted its recognition")
                tables.append({"page": page_index + 1, "region_index": region["index"], "recognition_index": target, "bbox": region["bbox"], "source": source, "rows": rows, "merges": merges})
            for index, item in enumerate(recognized):
                if index in consumed:
                    continue
                if regions and item["bbox"] is None:
                    raise PaddleOCRFormatError("PaddleOCR table cannot be spatially matched")
                rows, merges = parse_table(item["html"])
                tables.append({"page": page_index + 1, "region_index": None, "recognition_index": index, "bbox": item["bbox"], "source": "paddle_html_unlocated", "rows": rows, "merges": merges})
    return tables


def continuation_candidates(tables: list[dict]) -> list[dict]:
    """Report only repeat-header, equal-column continuations; never silently join."""
    links = []
    for index, (left, right) in enumerate(zip(tables, tables[1:]), 1):
        if left["page"] + 1 != right["page"]:
            continue
        a, b = left["rows"], right["rows"]
        if a and b and len(a[0]) > 1 and a[0] == b[0] and any(text.strip() for text in a[0]):
            if all(len(row) == len(a[0]) for row in a + b):
                links.append({"from_page": left["page"], "to_page": right["page"], "from_table": index, "to_table": index + 1, "reason": "repeated_header", "proposed": True})
    return links


def joined_tables(evidence: dict) -> list[dict]:
    """Optional conservative cross-page join. Never change the source evidence."""
    original = [table for page in evidence["pages"] for table in page["tables"]]
    links = {link["to_table"]: link for link in evidence["continuation_candidates"] if link["reason"] == "repeated_header"}
    assembled = []
    for index, table in enumerate(original, 1):
        link = links.get(index)
        if link is None:
            assembled.append({**table, "rows": [list(row) for row in table["rows"]], "merges": list(table["merges"])})
            continue
        if link["from_table"] != index - 1 or not assembled:
            raise PaddleOCRFormatError("Broken cross-page continuation evidence")
        current = assembled[-1]
        if current["merges"] or table["merges"]:
            assembled.append({**table, "rows": [list(row) for row in table["rows"]], "merges": list(table["merges"])})
            continue
        current["rows"].extend([list(row) for row in table["rows"][1:]])
        current.setdefault("continuation_pages", []).append(table["page"])
    return assembled
