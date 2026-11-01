"""Region resolution helpers for per-page extraction."""

from __future__ import annotations

from typing import Protocol

from ..extractors.pdf import TextBox
from .spec import RegionMode, RegionSpec


class RegionTextExtractor(Protocol):
    def extract_text_boxes(self, file_content: bytes, *, selected_pages: set[int] | None = None) -> list[TextBox]: ...


def resolve_table_region(
    file_content: bytes,
    *,
    page_index: int,
    region: RegionSpec,
    pdf_extractor: RegionTextExtractor,
) -> tuple[float | None, float, float | None, float] | None:
    if region.mode == RegionMode.FULL_PAGE:
        return None

    if region.mode == RegionMode.FIXED_BOX:
        return region.fixed_box

    if region.mode == RegionMode.MARKERS_Y:
        y_region = _find_y_region(
            file_content,
            page=page_index,
            start_markers=region.start_markers,
            end_markers=region.end_markers,
            pdf_extractor=pdf_extractor,
        )
        if y_region is None:
            if region.fallback_to_full_page:
                return None
            raise ValueError(f"Could not resolve markers_y region for page={page_index}")
        return (None, y_region[0], None, y_region[1])

    return None


def _find_y_region(
    file_content: bytes,
    *,
    page: int,
    start_markers: tuple[str, ...],
    end_markers: tuple[str, ...],
    pdf_extractor: RegionTextExtractor,
) -> tuple[float, float] | None:
    boxes = pdf_extractor.extract_text_boxes(file_content, selected_pages={page})
    page_boxes = [box for box in boxes if box.page == page]
    if not page_boxes:
        return None

    start_y_bottoms: list[float] = []
    end_y_tops: list[float] = []

    for box in page_boxes:
        if any(marker in box.text for marker in start_markers):
            start_y_bottoms.append(box.y1)
        if any(marker in box.text for marker in end_markers):
            end_y_tops.append(box.y0)

    if not start_y_bottoms or not end_y_tops:
        return None

    y0 = max(start_y_bottoms)
    y1 = min(end_y_tops)
    if y1 <= y0:
        return None

    return (y0, y1)
