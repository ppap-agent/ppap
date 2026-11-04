"""Opt-in PaddleOCR layout API evaluation. Not used by the production PDF pipeline.

The historical PP-StructureV3 HTTP contract is retained, but its deprecated
Paddle -> Gemma processing chain and shared OCR cache are not reinstated.
Only explicit callers can send a local PDF to this API.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import fitz
import requests
from openpyxl import Workbook, load_workbook

from documents.files import parse_file_size
from documents.ocr.env import max_file_size

MAX_TABLE_ROWS = 2000
MAX_TABLE_COLS = 256
MAX_TABLE_CELLS = 100_000
MAX_CELL_CHARS = 32_767


class PaddleOCRFormatError(ValueError):
    """Missing, truncated or contradictory OCR evidence; do not publish output."""


@dataclass(frozen=True)
class PaddleOCRStats:
    pages: int
    tables: int
    text_blocks: int
    seals: int


class PaddleStructureService:
    """Historical PP-StructureV3 /layout-parsing JSON-over-HTTP client.

    This is an explicit opt-in client, not an OCR adapter registered in the app.
    Endpoint must be the complete URL; credentials never enter the payload.
    """

    def __init__(self, endpoint: str, *, token: str = "", timeout: int = 300) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("PaddleOCR endpoint must be an HTTP(S) URL without credentials or fragment")
        if timeout <= 0:
            raise ValueError("PaddleOCR timeout must be positive")
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout

    def analyze_full_pdf(self, file_path: str | Path) -> dict:
        path, page_count = _validate_pdf(file_path)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        payload = {
            "file": base64.b64encode(path.read_bytes()).decode("ascii"),
            "fileType": 0,
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "useChartRecognition": False,
            "useSealRecognition": True,
            "useTableRecognition": True,
        }
        try:
            response = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaddleOCRFormatError(f"PaddleOCR request failed ({type(exc).__name__})") from None
        if not isinstance(body, dict):
            raise PaddleOCRFormatError("PaddleOCR response is not an object")
        result = body.get("result")
        _require_pages(result, page_count)
        return result


def _validate_pdf(file_path: str | Path) -> tuple[Path, int]:
    path = Path(file_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("PaddleOCR input must be a local PDF file")
    if path.stat().st_size > parse_file_size(max_file_size()):
        raise ValueError("PaddleOCR input exceeds MAX_FILE_SIZE")
    with fitz.open(path) as pdf:
        if not pdf.is_pdf or pdf.needs_pass or pdf.page_count == 0:
            raise ValueError("PaddleOCR input must be a readable nonempty PDF")
        return path, pdf.page_count


def _require_pages(result: object, page_count: int) -> list[dict]:
    if not isinstance(result, dict) or not isinstance(result.get("dataInfo"), dict):
        raise PaddleOCRFormatError("PaddleOCR response lacks required metadata")
    pages = result.get("layoutParsingResults")
    if not isinstance(pages, list) or len(pages) != page_count:
        raise PaddleOCRFormatError("PaddleOCR returned an incomplete PDF page set")
    if any(not isinstance(page, dict) or not isinstance(page.get("prunedResult"), dict) for page in pages):
        raise PaddleOCRFormatError("PaddleOCR returned an invalid page")
    return pages


class _TableHTML(HTMLParser):
    """Parse Paddle pred_html while retaining HTML row/column spans."""

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
            spans: list[int] = []
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
        merges: list[tuple[int, int, int, int]] = []
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


def _parse_table(markup: str) -> tuple[list[list[str]], list[tuple[int, int, int, int]]]:
    if not markup or len(markup) > 2_000_000:
        raise PaddleOCRFormatError("PaddleOCR table HTML is missing or too large")
    parser = _TableHTML()
    parser.feed(markup)
    parser.close()
    return parser.to_grid()


def _blocks(pruned: dict, name: str) -> list:
    value = pruned.get(name, [])
    if not isinstance(value, list):
        raise PaddleOCRFormatError(f"PaddleOCR {name} is not an array")
    return value


def export_paddle_result(result: dict, *, page_count: int, xlsx_path: str | Path, html_path: str | Path | None = None) -> PaddleOCRStats:
    """Export raw PP-StructureV3 evidence; no LLM, route switch or shared cache."""
    pages = _require_pages(result, page_count)
    workbook = Workbook()
    workbook.remove(workbook.active)
    evidence: list[tuple[int, str, str]] = []
    table_number = 0
    for page_no, page in enumerate(pages, 1):
        pruned = page["prunedResult"]
        recognized_tables = _blocks(pruned, "table_res_list")
        layout = pruned.get("layout_det_res", {})
        if not isinstance(layout, dict):
            raise PaddleOCRFormatError("PaddleOCR layout data is invalid")
        boxes = layout.get("boxes", [])
        if not isinstance(boxes, list):
            raise PaddleOCRFormatError("PaddleOCR layout boxes are invalid")
        if not recognized_tables and any(isinstance(box, dict) and box.get("label") == "table" for box in boxes):
            raise PaddleOCRFormatError("PaddleOCR detected a table but omitted its recognition")
        for table in recognized_tables:
            if not isinstance(table, dict) or not isinstance(table.get("pred_html"), str):
                raise PaddleOCRFormatError("PaddleOCR table is missing pred_html")
            grid, merges = _parse_table(table["pred_html"])
            table_number += 1
            sheet = workbook.create_sheet(f"Table_{table_number}")
            for row in grid:
                sheet.append(row)
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str) and cell.value.startswith("="):
                        cell.data_type = "s"  # OCR text must never become an Excel formula.
            for start_r, start_c, end_r, end_c in merges:
                sheet.merge_cells(start_row=start_r, start_column=start_c, end_row=end_r, end_column=end_c)
        for block in _blocks(pruned, "parsing_res_list"):
            if not isinstance(block, dict):
                raise PaddleOCRFormatError("PaddleOCR text block is invalid")
            if block.get("block_label") == "table":
                continue
            text = block.get("block_content", "")
            if not isinstance(text, str):
                raise PaddleOCRFormatError("PaddleOCR text block is invalid")
            if text.strip():
                evidence.append((page_no, "text", text.strip()))
        for seal in _blocks(pruned, "seal_res_list"):
            if not isinstance(seal, dict) or not isinstance(seal.get("rec_texts", []), list):
                raise PaddleOCRFormatError("PaddleOCR seal block is invalid")
            recognized = seal.get("rec_texts", [])
            if any(not isinstance(text, str) for text in recognized):
                raise PaddleOCRFormatError("PaddleOCR seal text is invalid")
            evidence.append((page_no, "seal", " ".join(recognized).strip()))

    if not table_number and not evidence:
        raise PaddleOCRFormatError("PaddleOCR returned no recognized evidence")
    if evidence:
        sheet = workbook.create_sheet("OCR_Text")
        sheet.append(["Page", "Type", "Content"])
        for page_no, kind, value in evidence:
            if len(value) > MAX_CELL_CHARS:
                raise PaddleOCRFormatError("PaddleOCR text exceeds Excel size limit")
            sheet.append([page_no, kind, value])
            if value.startswith("="):
                sheet.cell(row=sheet.max_row, column=3).data_type = "s"

    output = Path(xlsx_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    html_output = Path(html_path) if html_path is not None else None
    if html_output is not None:
        html_output.parent.mkdir(parents=True, exist_ok=True)
    pending: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as tmp:
            staged_xlsx = Path(tmp.name)
        pending.append(staged_xlsx)
        workbook.save(staged_xlsx)
        verified = load_workbook(staged_xlsx, read_only=True)
        try:
            if not verified.sheetnames:
                raise PaddleOCRFormatError("PaddleOCR workbook is empty")
        finally:
            verified.close()
        if html_output is not None:
            with tempfile.NamedTemporaryFile(dir=html_output.parent, suffix=".html", mode="w", encoding="utf-8", delete=False) as tmp:
                staged_html = Path(tmp.name)
                for page_no, kind, value in evidence:
                    tmp.write(f'<p data-page="{page_no}" class="{kind}">{html.escape(value, quote=True)}</p>\n')
            pending.append(staged_html)
            os.replace(staged_html, html_output)
        os.replace(staged_xlsx, output)
    finally:
        workbook.close()
        for path in pending:
            path.unlink(missing_ok=True)

    return PaddleOCRStats(pages=page_count, tables=table_number, text_blocks=sum(k == "text" for _, k, _ in evidence), seals=sum(k == "seal" for _, k, _ in evidence))


def convert_pdf_with_paddle(
    pdf_path: str | Path,
    *,
    xlsx_path: str | Path,
    endpoint: str | None = None,
    token: str = "",
    html_path: str | Path | None = None,
    response: dict | None = None,
) -> PaddleOCRStats:
    """Explicit evaluation entrypoint; response allows offline replay of saved JSON."""
    source, pages = _validate_pdf(pdf_path)
    if response is None:
        if not endpoint:
            raise ValueError("PaddleOCR endpoint is required unless replaying a response")
        result = PaddleStructureService(endpoint, token=token).analyze_full_pdf(source)
    else:
        result = response.get("result") if isinstance(response, dict) and "result" in response else response
    return export_paddle_result(result, page_count=pages, xlsx_path=xlsx_path, html_path=html_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit, isolated PaddleOCR API evaluation (not connected to PPAP audit)")
    parser.add_argument("--pdf", type=Path, required=True, help="Local source PDF")
    parser.add_argument("--xlsx", type=Path, required=True, help="Destination XLSX")
    parser.add_argument("--html", type=Path, help="Optional escaped OCR text / seal HTML")
    parser.add_argument("--endpoint", help="Full PaddleOCR /layout-parsing URL (or PADDLE_OCR_URL)")
    parser.add_argument("--response-json", type=Path, help="Replay previously saved API JSON instead of calling the network")
    args = parser.parse_args()
    if args.response_json and args.endpoint:
        parser.error("--response-json cannot be combined with --endpoint")
    endpoint = args.endpoint or (None if args.response_json else os.environ.get("PADDLE_OCR_URL"))
    if not args.response_json and not endpoint:
        parser.error("--endpoint or PADDLE_OCR_URL is required unless --response-json is provided")
    response = json.loads(args.response_json.read_text(encoding="utf-8")) if args.response_json else None
    result = convert_pdf_with_paddle(
        args.pdf,
        xlsx_path=args.xlsx,
        html_path=args.html,
        endpoint=endpoint,
        token=os.environ.get("PADDLE_OCR_TOKEN", ""),
        response=response,
    )
    print(f"Paddle OCR completed: pages={result.pages}, tables={result.tables}, text_blocks={result.text_blocks}, seals={result.seals}")


if __name__ == "__main__":
    main()
