"""Source-bound PP-StructureV3 layout and reading-order evidence."""

from __future__ import annotations

import math
from typing import Any, Literal

from .client import PaddleOCRFormatError, require_pages


def box(value: object) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise PaddleOCRFormatError("PaddleOCR coordinate must contain four numbers")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in value):
        raise PaddleOCRFormatError("PaddleOCR coordinate is not finite")
    x0, y0, x1, y1 = map(float, value)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise PaddleOCRFormatError("PaddleOCR coordinate is invalid")
    return [x0, y0, x1, y1]


def iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    overlap = ix * iy
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    return overlap / (area_a + area_b - overlap) if overlap else 0.0


def blocks(pruned: dict[str, Any], name: str) -> list:
    value = pruned.get(name, [])
    if not isinstance(value, list):
        raise PaddleOCRFormatError(f"PaddleOCR {name} is not an array")
    return value


def diagnostic(diagnostics: list[dict] | None, *, element: str, page: int, index: int | None, message: str, outcome: Literal["error", "unknown"] = "error") -> None:
    if diagnostics is None:
        raise PaddleOCRFormatError(message)
    suffix = "unknown" if outcome == "unknown" else "invalid"
    diagnostics.append({"code": f"paddle.{element}.{suffix}", "outcome": outcome, "message": message, "stage": "structure", "element": element, "page": page, "index": index})


def _overall_words(pruned: dict, page: int, diagnostics: list[dict] | None) -> list[dict]:
    if "overall_ocr_res" not in pruned:
        return []
    overall = pruned["overall_ocr_res"]
    if not isinstance(overall, dict) or not isinstance(overall.get("rec_texts"), list):
        diagnostic(diagnostics, element="text", page=page, index=None, message="PaddleOCR overall text data is invalid")
        return []
    texts = overall["rec_texts"]
    arrays = {}
    for name in ("rec_boxes", "rec_polys", "rec_scores"):
        values = overall.get(name)
        if values is not None and (not isinstance(values, list) or len(values) != len(texts)):
            diagnostic(diagnostics, element="text", page=page, index=None, message=f"PaddleOCR {name} does not align with overall text indices")
            values = None
        arrays[name] = values
    words = []
    for index, text in enumerate(texts):
        if not isinstance(text, str):
            diagnostic(diagnostics, element="text", page=page, index=index, message="PaddleOCR overall text word is invalid")
            continue
        if not text.strip():
            continue
        bounds, polygon = None, None
        try:
            if arrays["rec_boxes"] is not None:
                bounds = box(arrays["rec_boxes"][index])
            if arrays["rec_polys"] is not None:
                value = arrays["rec_polys"][index]
                if value is not None:
                    if (
                        not isinstance(value, list)
                        or len(value) < 3
                        or any(not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in point) for point in value)
                    ):
                        raise PaddleOCRFormatError("PaddleOCR overall word polygon is invalid")
                    polygon = [[float(n) for n in point] for point in value]
                    polygon_bounds = box([min(point[0] for point in polygon), min(point[1] for point in polygon), max(point[0] for point in polygon), max(point[1] for point in polygon)])
                    if polygon_bounds is None:
                        raise PaddleOCRFormatError("PaddleOCR overall word polygon is unavailable")
                    if bounds is not None and any(abs(a - b) > 1 for a, b in zip(bounds, polygon_bounds)):
                        raise PaddleOCRFormatError("PaddleOCR overall word coordinates conflict")
                    bounds = bounds or polygon_bounds
        except PaddleOCRFormatError as exc:
            diagnostic(diagnostics, element="text", page=page, index=index, message=str(exc))
            bounds = None
        score = arrays["rec_scores"][index] if arrays["rec_scores"] is not None else None
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
            diagnostic(diagnostics, element="text", page=page, index=index, message="PaddleOCR overall word confidence is invalid")
            score = None
        words.append({"index": index, "source": "paddle_overall_ocr", "type": "ocr_word", "text": text, "bbox": bounds, "polygon": polygon, "score": score})
    return words


def page_layouts(result: dict, page_count: int, *, diagnostics: list[dict] | None = None, source_pages: list[int] | None = None) -> list[dict]:
    layouts = []
    for page_no, source in enumerate(require_pages(result, page_count), 1):
        original_page = source_pages[page_no - 1] if source_pages is not None else page_no
        pruned = source["prunedResult"]
        layout = pruned.get("layout_det_res", {})
        if not isinstance(layout, dict):
            diagnostic(diagnostics, element="layout", page=original_page, index=None, message="PaddleOCR layout data is invalid")
            layout = {}
        raw_regions = layout.get("boxes", [])
        if not isinstance(raw_regions, list):
            diagnostic(diagnostics, element="layout", page=original_page, index=None, message="PaddleOCR layout boxes are invalid")
            raw_regions = []
        regions = []
        for index, item in enumerate(raw_regions):
            if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                diagnostic(diagnostics, element="layout", page=original_page, index=index, message="PaddleOCR layout region is invalid")
                continue
            try:
                bounds = box(item.get("coordinate"))
            except PaddleOCRFormatError as exc:
                diagnostic(diagnostics, element="layout", page=original_page, index=index, message=str(exc))
                bounds = None
            regions.append({"index": index, "label": item["label"], "bbox": bounds})
        paragraphs = []
        try:
            text_blocks = blocks(pruned, "parsing_res_list")
        except PaddleOCRFormatError as exc:
            diagnostic(diagnostics, element="text", page=original_page, index=None, message=str(exc))
            text_blocks = []
        for index, item in enumerate(text_blocks):
            if not isinstance(item, dict) or not isinstance(item.get("block_label", "text"), str) or not isinstance(item.get("block_content", ""), str):
                diagnostic(diagnostics, element="text", page=original_page, index=index, message="PaddleOCR text block is invalid")
                continue
            try:
                bounds = box(item.get("block_bbox"))
            except PaddleOCRFormatError as exc:
                diagnostic(diagnostics, element="text", page=original_page, index=index, message=str(exc))
                bounds = None
            paragraphs.append({"index": index, "source": "paddle_parsing", "type": item.get("block_label", "text"), "text": item.get("block_content", ""), "bbox": bounds})
        # Only the canonical per-page result is read. Top-level ocrResults is a
        # response alias, not another recognition or a second set of words.
        paragraphs.extend(_overall_words(pruned, original_page, diagnostics))
        # Preserve every source index, even when sorting for reading order.
        paragraphs.sort(key=lambda item: ((int(item["bbox"][1] / 20), item["bbox"][0]) if item["bbox"] else (math.inf, item["index"])))
        layouts.append({"page": page_no, "regions": regions, "texts": paragraphs})
    return layouts
