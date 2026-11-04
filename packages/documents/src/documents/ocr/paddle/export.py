"""One-shot local export: original Paddle response, evidence JSON, and Excel."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from .client import PaddleOCRFormatError, PaddleStructureService, validate_pdf
from .evidence import build_evidence
from .tables import MAX_CELL_CHARS


@dataclass(frozen=True)
class PaddleOCRStats:
    pages: int
    tables: int
    text_blocks: int
    seals: int
    output_dir: Path
    status: str
    artifacts: tuple[str, ...]


def _workbook(evidence: dict) -> Workbook | None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    table_number = 0
    text_rows: list[tuple[int, str, str]] = []

    def exportable(values: list[str], page: dict, element: str, index: int) -> bool:
        if all(
            len(value) <= MAX_CELL_CHARS and not ILLEGAL_CHARACTERS_RE.search(value) and not any(0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF} for char in value) for value in values
        ):
            return True
        # The JSON fact remains intact. Excel constraints are an export-local
        # limitation, never grounds to truncate source evidence or lose peers.
        diagnostic = {
            "code": "paddle.export.cell_unavailable",
            "stage": "conversion",
            "element": element,
            "page": page["page"],
            "index": index,
            "message": "Recognized content cannot be represented as an Excel cell; the complete fact remains in JSON",
        }
        evidence["diagnostics"].append(diagnostic)
        page["diagnostics"].append(diagnostic)
        evidence["status"] = "partial"
        return False

    for page in evidence["pages"]:
        for table_index, table in enumerate(page["tables"]):
            source_index = table.get("recognition_index")
            if source_index is None:
                source_index = table.get("region_index")
            if not exportable([cell for row in table["rows"] for cell in row], page, "table", source_index if source_index is not None else table_index):
                continue
            table_number += 1
            sheet = workbook.create_sheet(f"Table_{table_number}")
            for row in table["rows"]:
                sheet.append(row)
                for cell in sheet[sheet.max_row]:
                    if cell.data_type == "f":
                        cell.data_type = "s"  # Untrusted OCR text must never execute as a formula.
            for r0, c0, r1, c1 in table["merges"]:
                sheet.merge_cells(start_row=r0, start_column=c0, end_row=r1, end_column=c1)
        for block_index, block in enumerate(page["texts"]):
            if block["type"] == "table":
                continue
            if not exportable([block["type"], block["text"]], page, "text", block.get("index", block_index)):
                continue
            content = block["text"].strip()
            if content:
                text_rows.append((page["page"], block["type"], content))
        for seal_index, seal in enumerate(page["seals"]):
            text = " ".join(seal["recognized_text"])
            if not exportable([text], page, "seal", seal_index):
                continue
            if text or seal.get("detected") is True:
                text_rows.append((page["page"], "seal", text or "检测到印章（内容未识别）"))

    if not table_number:
        workbook.close()
        return None
    if text_rows:
        sheet = workbook.create_sheet("OCR_Text")
        sheet.append(["Page", "Type", "Content"])
        for page, kind, content in text_rows:
            sheet.append([page, kind, content])
            if sheet.cell(sheet.max_row, 3).data_type == "f":
                sheet.cell(sheet.max_row, 3).data_type = "s"
    return workbook


def evaluate_pdf(
    pdf_path: str | Path,
    out_root: str | Path,
    *,
    response: dict | None = None,
    endpoint: str | None = None,
    token: str = "",
    page_number: int | None = None,
) -> PaddleOCRStats:
    """Publish a unique run directory, even when multiple jobs share one root."""
    source, page_count = validate_pdf(pdf_path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if page_number is not None and (type(page_number) is not int or page_number < 1 or page_number > page_count):
        raise ValueError("Selected PDF page is out of range")
    root = Path(out_root).expanduser().resolve()
    result = None
    selected_count = 1 if page_number is not None else page_count
    try:
        if response is None:
            if page_number is None:
                raise ValueError("Live PaddleOCR requires an explicit page_number")
            if not endpoint:
                raise ValueError("PaddleOCR endpoint is required unless replaying a response")
            result = PaddleStructureService(endpoint, token=token).analyze_page(source, page_number)
        else:
            result = response.get("result") if isinstance(response, dict) and "result" in response else response
        if not isinstance(result, dict):
            raise PaddleOCRFormatError("PaddleOCR response is not an object")
        evidence = build_evidence(result, page_count=selected_count, pdf_path=source, source_pages=[page_number] if page_number is not None else None)
        if evidence["source_sha256"] != source_hash:
            raise PaddleOCRFormatError("PaddleOCR source changed during evidence interpretation")
    except PaddleOCRFormatError:
        # Preserve the received data for explicit offline diagnosis, but never
        # expose arbitrary remote exception text as trusted document evidence.
        evidence = {
            "status": "error",
            "source_sha256": source_hash,
            "pages": [],
            "diagnostics": [
                {
                    "code": "paddle.response.invalid",
                    "stage": "conversion",
                    "element": "response",
                    "page": page_number,
                    "message": "PaddleOCR request or response could not provide reliable page evidence",
                }
            ],
        }
    workbook = _workbook(evidence) if any(page["tables"] for page in evidence["pages"]) else None
    artifacts = ["evidence.json"]
    if result is not None:
        artifacts.append("response.json")
    if workbook is not None:
        artifacts.append("tables.xlsx")
    evidence["artifacts"] = artifacts
    root.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".paddle-", dir=root))
    destination: Path | None = None
    try:
        if workbook is not None:
            try:
                workbook.save(staged / "tables.xlsx")
            finally:
                workbook.close()
            verified = load_workbook(staged / "tables.xlsx", read_only=True)
            verified.close()
        (staged / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8", errors="backslashreplace")
        if result is not None:
            (staged / "response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", errors="backslashreplace")
        for _ in range(10):
            candidate = root / f"paddle-{secrets.token_hex(16)}"
            try:
                candidate.mkdir(mode=0o700)  # Atomic reservation; never clobber another run.
            except FileExistsError:
                continue
            try:
                os.rename(staged, candidate)  # Publish all three files in one rename (POSIX).
            except BaseException:
                candidate.rmdir()
                raise
            destination = candidate
            break
        if destination is None:
            raise FileExistsError("Unable to reserve a unique PaddleOCR output directory")
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return PaddleOCRStats(
        pages=selected_count,
        tables=sum(len(page["tables"]) for page in evidence["pages"]),
        text_blocks=sum(block["type"] != "table" and bool(block["text"].strip()) for page in evidence["pages"] for block in page["texts"]),
        seals=sum(len(page["seals"]) for page in evidence["pages"]),
        output_dir=destination,
        status=evidence["status"],
        artifacts=tuple(artifacts),
    )
