"""Canonical, source-preserving PaddleOCR evidence for local comparison."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import fitz

from .client import PaddleOCRFormatError, page_dimensions, require_pages
from .layout import diagnostic, page_layouts
from .seals import link_region_words, seal_evidence
from .tables import recognize_tables


def _frames(result: dict, source: bytes | None, source_pages: list[int], *, strict: bool) -> list[dict | None]:
    frames: list[dict | None] = []
    with fitz.open(stream=source, filetype="pdf") if source is not None else fitz.open() as pdf:
        for index, page_no in enumerate(source_pages):
            try:
                width, height = page_dimensions(result, index)
            except PaddleOCRFormatError:
                if strict:
                    raise
                frames.append(None)
                continue
            frame = {"coordinate_system": "paddle_pixels", "width": width, "height": height}
            pruned = result["layoutParsingResults"][index]["prunedResult"]
            for key, value in (("width", width), ("height", height)):
                actual = pruned.get(key, value)
                if type(actual) not in (int, float) or not math.isfinite(actual) or abs(actual - value) > 1:
                    raise PaddleOCRFormatError("PaddleOCR page dimensions conflict with the response")
            if source is not None:
                if not 1 <= page_no <= len(pdf):
                    raise PaddleOCRFormatError("PaddleOCR original page identity is invalid")
                page = pdf[page_no - 1]
                if strict and not math.isclose(width / height, page.rect.width / page.rect.height, rel_tol=0.02):
                    raise PaddleOCRFormatError("PaddleOCR page dimensions do not match the original page")
                unrotated = page.rect * page.derotation_matrix
                frame.update(
                    {
                        "pdf_coordinate_system": "pdf_points_unrotated",
                        "pdf_width": unrotated.width,
                        "pdf_height": unrotated.height,
                        "pdf_display_width": page.rect.width,
                        "pdf_display_height": page.rect.height,
                        "rotation": page.rotation,
                        "derotation_matrix": list(page.derotation_matrix),
                    }
                )
            frames.append(frame)
    return frames


def _locate(item: dict, frame: dict | None, diagnostics: list[dict], *, element: str, page: int) -> None:
    item["pdf_bbox"] = None
    bounds = item.get("bbox")
    if bounds is None or frame is None or "derotation_matrix" not in frame:
        diagnostic(diagnostics, element=element, page=page, index=item.get("index", item.get("recognition_index")), message="PaddleOCR original location is unavailable", outcome="unknown")
        return
    if bounds[2] > frame["width"] or bounds[3] > frame["height"]:
        diagnostic(diagnostics, element=element, page=page, index=item.get("index", item.get("recognition_index")), message="PaddleOCR coordinate is outside its source page", outcome="unknown")
        return
    rectangle = fitz.Rect(
        bounds[0] * frame["pdf_display_width"] / frame["width"],
        bounds[1] * frame["pdf_display_height"] / frame["height"],
        bounds[2] * frame["pdf_display_width"] / frame["width"],
        bounds[3] * frame["pdf_display_height"] / frame["height"],
    )
    original = rectangle * fitz.Matrix(frame["derotation_matrix"])
    item["pdf_bbox"] = [round(value, 6) for value in original]


def build_evidence(result: dict, *, page_count: int, pdf_path: str | Path | None = None, source_pages: list[int] | None = None) -> dict:
    """Keep usable facts even when another element fails; never infer an audit result."""
    require_pages(result, page_count)
    mapping = source_pages if source_pages is not None else list(range(1, page_count + 1))
    if len(mapping) != page_count or any(type(n) is not int or n < 1 for n in mapping) or len(set(mapping)) != len(mapping):
        raise PaddleOCRFormatError("PaddleOCR original page identity is invalid")
    if source_pages is not None and pdf_path is None:
        raise PaddleOCRFormatError("PaddleOCR original source is required for page mapping")
    source = Path(pdf_path).read_bytes() if pdf_path is not None else None
    source_hash = hashlib.sha256(source).hexdigest() if source is not None else None
    frames = _frames(result, source, mapping, strict=source_pages is not None)
    diagnostics: list[dict] = []
    layouts = page_layouts(result, page_count, diagnostics=diagnostics, source_pages=mapping)
    tables = recognize_tables(pdf_path, result, layouts, diagnostics=diagnostics, source_pages=mapping)
    pages = []
    for page_index, layout in enumerate(layouts):
        pruned = result["layoutParsingResults"][page_index]["prunedResult"]
        original_page = mapping[page_index]
        page_tables = [table for table in tables if table["page"] == original_page]
        seals = seal_evidence(pruned, layout["regions"], original_page, diagnostics=diagnostics)
        for element, items in (("layout", layout["regions"]), ("text", layout["texts"]), ("table", page_tables), ("seal", seals)):
            for item in items:
                _locate(item, frames[page_index], diagnostics, element=element, page=original_page)
        link_region_words(seals, layout["texts"], original_page, diagnostics)
        pages.append(
            {
                "page": original_page,
                "frame": frames[page_index],
                "regions": layout["regions"],
                "texts": layout["texts"],
                "seals": seals,
                "tables": page_tables,
            }
        )
    for page in pages:
        page["diagnostics"] = [item for item in diagnostics if item["page"] == page["page"]]
    if pdf_path is not None and hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest() != source_hash:
        raise PaddleOCRFormatError("PaddleOCR source changed while interpreting the response")
    has_facts = any(page["tables"] or page["seals"] or any(block["text"].strip() for block in page["texts"]) for page in pages)
    return {
        "schema_version": "paddle_layout_evidence_v1",
        "engine": "paddleocr_ppstructure_v3",
        "source_sha256": source_hash,
        "status": ("partial" if diagnostics else "complete") if has_facts else "error",
        "diagnostics": diagnostics,
        "pages": pages,
    }
