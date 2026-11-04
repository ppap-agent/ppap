"""Isolated local OCR artifacts, without product cache or model dependencies."""

from __future__ import annotations

import html
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz
from openpyxl import Workbook, load_workbook

from .client import PaddleOCRFormatError, PaddleStructureService, validate_pdf
from .evidence import build_evidence
from .tables import MAX_CELL_CHARS, joined_tables


@dataclass(frozen=True)
class PaddleOCRStats:
    pages: int
    tables: int
    text_blocks: int
    seals: int


def _publish_artifact(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as temp:
        staged = Path(temp.name)
    try:
        if isinstance(data, str):
            staged.write_text(data, encoding="utf-8")
        else:
            staged.write_bytes(data)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _workbook(evidence: dict, *, join_continuations: bool = False) -> tuple[Workbook, list[tuple[int, str, str]]]:
    workbook = Workbook()
    workbook.remove(workbook.active)
    annotations: list[tuple[int, str, str]] = []
    table_number = 0
    table_data = joined_tables(evidence) if join_continuations else [table for page in evidence["pages"] for table in page["tables"]]
    for table in table_data:
        table_number += 1
        sheet = workbook.create_sheet(f"Table_{table_number}")
        for row in table["rows"]:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith(("=", "+", "-", "@")):
                    cell.data_type = "s"
        for r0, c0, r1, c1 in table["merges"]:
            sheet.merge_cells(start_row=r0, start_column=c0, end_row=r1, end_column=c1)
    for page in evidence["pages"]:
        for item in page["texts"]:
            text = item["text"].strip()
            if item["type"] != "table" and text:
                annotations.append((page["page"], item["type"], text))
        for seal in page["seals"]:
            annotations.append((page["page"], "seal", " ".join(seal["recognized_text"])))
    if not table_number and not annotations:
        raise PaddleOCRFormatError("PaddleOCR returned no recognized evidence")
    if annotations:
        sheet = workbook.create_sheet("OCR_Text")
        sheet.append(["Page", "Type", "Content"])
        for page_no, kind, value in annotations:
            if len(value) > MAX_CELL_CHARS:
                raise PaddleOCRFormatError("PaddleOCR text exceeds Excel size limit")
            sheet.append([page_no, kind, value])
            if value.startswith(("=", "+", "-", "@")):
                sheet.cell(sheet.max_row, 3).data_type = "s"
    return workbook, annotations


def _crop_seals(pdf_path: str | Path, result: dict, evidence: dict, crop_dir: Path) -> None:
    sizes = result["dataInfo"].get("pages", [])
    with fitz.open(pdf_path) as pdf:
        for page in evidence["pages"]:
            index = page["page"] - 1
            metadata = sizes[index] if isinstance(sizes, list) and index < len(sizes) else {}
            if not isinstance(metadata, dict):
                raise PaddleOCRFormatError("PaddleOCR page metadata is invalid")
            iw, ih = float(metadata.get("width") or 1654), float(metadata.get("height") or 2339)
            if iw <= 0 or ih <= 0:
                raise PaddleOCRFormatError("PaddleOCR image dimensions are invalid")
            pdf_page = pdf[index]
            for seal_index, seal in enumerate(page["seals"], 1):
                coords = seal["bbox"]
                if coords is None:
                    continue
                clip = fitz.Rect(coords[0] * pdf_page.rect.width / iw, coords[1] * pdf_page.rect.height / ih, coords[2] * pdf_page.rect.width / iw, coords[3] * pdf_page.rect.height / ih)
                clip &= pdf_page.rect
                if clip.is_empty:
                    raise PaddleOCRFormatError("PaddleOCR seal is outside source page")
                pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
                if pixmap.width * pixmap.height > 8_000_000:
                    raise PaddleOCRFormatError("PaddleOCR seal crop is too large")
                _publish_artifact(crop_dir / f"page-{index + 1:03}-seal-{seal_index:03}.png", pixmap.tobytes("png"))


def _non_table_images(pdf_path: str | Path, result: dict, evidence: dict, directory: Path) -> None:
    """Reproduce historical whole-width table-band removal for non-table preview images.

    These are previews, not inferred text, and do not replace the original PDF.
    """
    from PIL import Image

    sizes = result["dataInfo"].get("pages", [])
    with fitz.open(pdf_path) as pdf:
        for page in evidence["pages"]:
            if not any(region["label"] == "paragraph_title" for region in page["regions"]):
                continue
            idx = page["page"] - 1
            meta = sizes[idx] if isinstance(sizes, list) and idx < len(sizes) else {}
            if not isinstance(meta, dict):
                raise PaddleOCRFormatError("PaddleOCR page metadata is invalid")
            width, height = float(meta.get("width") or 1654), float(meta.get("height") or 2339)
            if width <= 0 or height <= 0 or width * height > 20_000_000:
                raise PaddleOCRFormatError("PaddleOCR preview dimensions are invalid")
            pdf_page = pdf[idx]
            pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(width / pdf_page.rect.width, height / pdf_page.rect.height), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            intervals = []
            for region in page["regions"]:
                if region["label"] != "table" or region["bbox"] is None:
                    continue
                y0 = max(0, min(pixmap.height, int(region["bbox"][1] * pixmap.height / height)))
                y1 = max(0, min(pixmap.height, int(region["bbox"][3] * pixmap.height / height)))
                if y0 < y1:
                    intervals.append((y0, y1))
            intervals.sort()
            kept = []
            at = 0
            for start, stop in intervals:
                if start > at:
                    kept.append((at, start))
                at = max(at, stop)
            if at < pixmap.height:
                kept.append((at, pixmap.height))
            new_height = sum(stop - start for start, stop in kept)
            if new_height == 0:
                continue
            output = Image.new("RGB", (pixmap.width, new_height), "white")
            cursor = 0
            for start, stop in kept:
                output.paste(image.crop((0, start, pixmap.width, stop)), (0, cursor))
                cursor += stop - start
            buffer = io.BytesIO()
            output.save(buffer, format="PNG")
            _publish_artifact(directory / f"page-{page['page']:03}-non-table.png", buffer.getvalue())


def export_paddle_result(
    result: dict,
    *,
    page_count: int,
    xlsx_path: str | Path,
    html_path: str | Path | None = None,
    evidence_json_path: str | Path | None = None,
    pdf_path: str | Path | None = None,
    seal_crop_dir: str | Path | None = None,
    non_table_dir: str | Path | None = None,
    raw_json_path: str | Path | None = None,
    join_continuations: bool = False,
) -> PaddleOCRStats:
    """Export Paddle evidence; only caller-named local artifacts can be changed."""
    if (seal_crop_dir is not None or non_table_dir is not None) and pdf_path is None:
        raise ValueError("Image cropping requires a local source PDF")
    paths = [Path(xlsx_path), *(Path(x) for x in (html_path, evidence_json_path, raw_json_path) if x is not None)]
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("OCR output paths must be distinct")
    if any(path.suffix.lower() not in {".xlsx", ".html", ".json"} for path in paths):
        raise ValueError("OCR outputs must be named XLSX, HTML or JSON files")
    if pdf_path is not None and any(path.resolve() == Path(pdf_path).resolve() for path in paths):
        raise ValueError("OCR outputs cannot overwrite input PDF")
    evidence = build_evidence(result, page_count=page_count, pdf_path=pdf_path)
    workbook, annotations = _workbook(evidence, join_continuations=join_continuations)
    pending = None
    try:
        output = Path(xlsx_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as temp:
            pending = Path(temp.name)
        workbook.save(pending)
        verified = load_workbook(pending, read_only=True)
        try:
            if not verified.sheetnames:
                raise PaddleOCRFormatError("PaddleOCR workbook is empty")
        finally:
            verified.close()
    finally:
        workbook.close()
    try:
        if seal_crop_dir is not None:
            _crop_seals(pdf_path, result, evidence, Path(seal_crop_dir))
        if non_table_dir is not None:
            _non_table_images(pdf_path, result, evidence, Path(non_table_dir))
        if evidence_json_path is not None:
            _publish_artifact(Path(evidence_json_path), json.dumps(evidence, ensure_ascii=False, indent=2))
        if raw_json_path is not None:
            _publish_artifact(Path(raw_json_path), json.dumps(result, ensure_ascii=False, indent=2))
        if html_path is not None:
            rendered = []
            for page_no, kind, value in annotations:
                rendered.append(f'<p data-page="{page_no}" class="{html.escape(kind, quote=True)}">{html.escape(value, quote=True)}</p>')
            for page in evidence["pages"]:
                for seal in page["seals"]:
                    if seal["text_status"] == "unreadable":
                        rendered.append(f'<p data-page="{page["page"]}" class="seal-unreadable">检测到印章（内容未识别）</p>')
            _publish_artifact(Path(html_path), "\n".join(rendered))
        os.replace(pending, Path(xlsx_path))
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
    return PaddleOCRStats(
        pages=page_count,
        tables=sum(len(page["tables"]) for page in evidence["pages"]),
        text_blocks=sum(kind != "seal" for _, kind, _ in annotations),
        seals=sum(len(page["seals"]) for page in evidence["pages"]),
    )


def evaluate_pdf(
    pdf_path: str | Path,
    *,
    xlsx_path: str | Path,
    endpoint: str | None = None,
    token: str = "",
    html_path: str | Path | None = None,
    evidence_json_path: str | Path | None = None,
    seal_crop_dir: str | Path | None = None,
    non_table_dir: str | Path | None = None,
    raw_json_path: str | Path | None = None,
    response: dict | None = None,
    join_continuations: bool = False,
) -> PaddleOCRStats:
    source, page_count = validate_pdf(pdf_path)
    if response is None:
        if not endpoint:
            raise ValueError("PaddleOCR endpoint is required unless replaying a response")
        result = PaddleStructureService(endpoint, token=token).analyze_full_pdf(source)
    else:
        result = response.get("result") if isinstance(response, dict) and "result" in response else response
    return export_paddle_result(
        result,
        page_count=page_count,
        xlsx_path=xlsx_path,
        html_path=html_path,
        evidence_json_path=evidence_json_path,
        seal_crop_dir=seal_crop_dir,
        non_table_dir=non_table_dir,
        raw_json_path=raw_json_path,
        pdf_path=source,
        join_continuations=join_continuations,
    )
