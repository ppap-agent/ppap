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
            paragraphs.append({"index": index, "type": item.get("block_label", "text"), "text": item.get("block_content", ""), "bbox": bounds})
        # Preserve every source index, even when sorting for reading order.
        paragraphs.sort(key=lambda item: ((int(item["bbox"][1] / 20), item["bbox"][0]) if item["bbox"] else (math.inf, item["index"])))
        layouts.append({"page": page_no, "regions": regions, "texts": paragraphs})
    return layouts
