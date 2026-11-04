"""One-shot local export: original Paddle response, evidence JSON, and Excel."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook

from .client import PaddleOCRFormatError, PaddleStructureService, validate_pdf
from .evidence import build_evidence
from .tables import MAX_CELL_CHARS


@dataclass(frozen=True)
class PaddleOCRStats:
    pages: int
    tables: int
    text_blocks: int
    seals: int


def _workbook(evidence: dict) -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    table_number = 0
    text_rows: list[tuple[int, str, str]] = []
    for page in evidence["pages"]:
        for table in page["tables"]:
            table_number += 1
            sheet = workbook.create_sheet(f"Table_{table_number}")
            for row in table["rows"]:
                sheet.append(row)
                for cell in sheet[sheet.max_row]:
                    if cell.data_type == "f":
                        cell.data_type = "s"  # Untrusted OCR text must never execute as a formula.
            for r0, c0, r1, c1 in table["merges"]:
                sheet.merge_cells(start_row=r0, start_column=c0, end_row=r1, end_column=c1)
        for block in page["texts"]:
            content = block["text"].strip()
            if block["type"] != "table" and content:
                text_rows.append((page["page"], block["type"], content))
        for seal in page["seals"]:
            text_rows.append((page["page"], "seal", " ".join(seal["recognized_text"]) or "检测到印章（内容未识别）"))

    if not table_number and not text_rows:
        raise PaddleOCRFormatError("PaddleOCR returned no recognized evidence")
    if text_rows:
        sheet = workbook.create_sheet("OCR_Text")
        sheet.append(["Page", "Type", "Content"])
        for page, kind, content in text_rows:
            if len(content) > MAX_CELL_CHARS:
                raise PaddleOCRFormatError("PaddleOCR text exceeds Excel size limit")
            sheet.append([page, kind, content])
            if sheet.cell(sheet.max_row, 3).data_type == "f":
                sheet.cell(sheet.max_row, 3).data_type = "s"
    return workbook


def evaluate_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    response: dict | None = None,
    endpoint: str | None = None,
    token: str = "",
) -> PaddleOCRStats:
    """Create an entirely new output directory; never alter the input PDF or production cache."""
    source, page_count = validate_pdf(pdf_path)
    destination = Path(out_dir).expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"PaddleOCR output directory already exists: {destination}")
    if response is None:
        if not endpoint:
            raise ValueError("PaddleOCR endpoint is required unless replaying a response")
        result = PaddleStructureService(endpoint, token=token).analyze_full_pdf(source)
    else:
        result = response.get("result") if isinstance(response, dict) and "result" in response else response

    evidence = build_evidence(result, page_count=page_count, pdf_path=source)
    workbook = _workbook(evidence)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".paddle-", dir=destination.parent))
    try:
        try:
            workbook.save(staged / "tables.xlsx")
        finally:
            workbook.close()
        verified = load_workbook(staged / "tables.xlsx", read_only=True)
        verified.close()
        (staged / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        (staged / "response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.rename(staged, destination)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return PaddleOCRStats(
        pages=page_count,
        tables=sum(len(page["tables"]) for page in evidence["pages"]),
        text_blocks=sum(block["type"] != "table" and bool(block["text"].strip()) for page in evidence["pages"] for block in page["texts"]),
        seals=sum(len(page["seals"]) for page in evidence["pages"]),
    )
