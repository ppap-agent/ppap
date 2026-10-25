"""Structured multimodal PDF-page reading through the Responses protocol."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fitz
from ppap_core.settings import get_settings
from ppap_docops.files import parse_file_size

from .client import ResponsesEvidenceError, call_llm, call_llm_async

logger = logging.getLogger(__name__)

# One schema and one Responses request per page. Never guess missing evidence.
PAGE_SCHEMA: dict[str, Any] = {
    "name": "ppap_multimodal_pdf_page",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tables": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                        "merges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "start_row": {"type": "integer"},
                                    "start_col": {"type": "integer"},
                                    "end_row": {"type": "integer"},
                                    "end_col": {"type": "integer"},
                                },
                                "required": ["start_row", "start_col", "end_row", "end_col"],
                            },
                        },
                    },
                    "required": ["rows", "merges"],
                },
            },
            "paragraphs": {"type": "array", "items": {"type": "string"}},
            "seals": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tables", "paragraphs", "seals"],
    },
}

PAGE_PROMPT = (
    "Read this PPAP document page visually. Output JSON only, using the supplied schema. "
    "Preserve table row and column order exactly as shown and transcribe text without inventing values. "
    "Each visible table contains rows and merges. Preserve empty cells as empty strings. "
    "Use zero-based start_row/start_col/end_row/end_col for rectangular, genuinely merged cells "
    "whose physical borders span multiple rows or columns. Never infer merges from repeated text. "
    "Leave subordinate cells of each merge blank, and put its value only in the upper-left anchor. "
    "Capture non-table paragraphs separately in reading order. Put legible stamp/signature text "
    "in seals; if the mark is visible but unreadable, use an empty string. "
    "Return empty arrays for categories not present. Do not infer a PASS, approval, or signature."
)


def _validate_page(payload: Any) -> dict[str, Any]:
    """Fail closed on provider incompatibility rather than emitting false evidence."""
    if not isinstance(payload, dict) or set(payload) != {"tables", "paragraphs", "seals"}:
        raise ValueError("Invalid Responses page schema")
    tables, paragraphs, seals = payload["tables"], payload["paragraphs"], payload["seals"]
    if not isinstance(tables, list) or not isinstance(paragraphs, list) or not isinstance(seals, list):
        raise ValueError("Invalid Responses page collections")
    if any(not isinstance(x, str) for x in [*paragraphs, *seals]):
        raise ValueError("Invalid Responses page text")
    for table_index, table in enumerate(tables, 1):
        if not isinstance(table, dict) or set(table) != {"rows", "merges"}:
            raise ValueError("Invalid Responses table schema")
        rows, merges = table["rows"], table["merges"]
        if not isinstance(rows, list) or not isinstance(merges, list):
            raise ValueError("Invalid Responses table dimensions")
        for row in rows:
            if not isinstance(row, list) or any(not isinstance(cell, str) for cell in row):
                raise ValueError("Invalid Responses table cell")
        occupied: set[tuple[int, int]] = set()
        for merge in merges:
            if not isinstance(merge, dict) or set(merge) != {"start_row", "start_col", "end_row", "end_col"}:
                raise ValueError("Invalid Responses merged-cell structure")
            r0, c0, r1, c1 = (merge[key] for key in ("start_row", "start_col", "end_row", "end_col"))
            if any(type(index) is not int for index in (r0, c0, r1, c1)):
                raise ValueError("Invalid Responses merged-cell coordinate")
            if r0 < 0 or c0 < 0 or r0 > r1 or c0 > c1 or r1 >= len(rows):
                raise ValueError("Invalid Responses merged-cell range")
            if r0 == r1 and c0 == c1:
                raise ValueError("Invalid Responses single-cell merge")
            for row_index in range(r0, r1 + 1):
                if c1 >= len(rows[row_index]):
                    raise ValueError("Invalid Responses merged-cell width")
                for column_index in range(c0, c1 + 1):
                    coordinate = (row_index, column_index)
                    if coordinate in occupied:
                        raise ValueError("Invalid Responses overlapping merges")
                    occupied.add(coordinate)
                    if coordinate != (r0, c0) and rows[row_index][column_index]:
                        raise ValueError(f"Invalid Responses merge covers nonempty evidence (table={table_index}, anchor=({r0},{c0}), cell=({row_index},{column_index}))")
    return payload


def _grid_bounds(values: list[float]) -> list[float]:
    """Collapse minor PDF drawing-coordinate jitter without inventing borders."""
    marks: list[float] = []
    for value in sorted(values):
        if not marks or value - marks[-1] > 1.0:
            marks.append(value)
    return marks


def _boundary_index(value: float, bounds: list[float]) -> int:
    return next((i for i, mark in enumerate(bounds) if abs(value - mark) <= 1.0), -1)


def _native_table_candidates(page: fitz.Page) -> list[dict[str, Any]]:
    """Accept only real PDF vector-table cell rectangles as merge evidence.

    Geometry extraction and source text reading occur on the PDF owning thread.
    Model output is never allowed to create borders; unidentifiable grids stay
    unverified rather than being mapped to the wrong table.
    """
    try:
        tables = page.find_tables(strategy="lines").tables
    except Exception as exc:
        logger.info("PDF vector table geometry unavailable: error_type=%s", type(exc).__name__)
        return []

    candidates: list[dict[str, Any]] = []
    for table in tables:
        height, width = table.row_count, table.col_count
        if not 0 < height <= 500 or not 0 < width <= 100:
            continue
        cells = [list(row.cells) for row in table.rows]
        if len(cells) != height or any(len(row) != width for row in cells):
            continue
        rectangles = [rect for row in cells for rect in row if rect is not None]
        if not rectangles:
            continue
        xs = _grid_bounds([float(rect[i]) for rect in rectangles for i in (0, 2)])
        ys = _grid_bounds([float(rect[i]) for rect in rectangles for i in (1, 3)])
        if len(xs) != width + 1 or len(ys) != height + 1:
            continue

        merges: list[dict[str, int]] = []
        covered: set[tuple[int, int]] = set()
        valid = True
        for row_index, row in enumerate(cells):
            for col_index, rect in enumerate(row):
                if rect is None:
                    continue
                left, top, right, bottom = (_boundary_index(float(rect[i]), axis) for i, axis in ((0, xs), (1, ys), (2, xs), (3, ys)))
                if left != col_index or top != row_index or right <= left or bottom <= top:
                    valid = False
                    break
                if right > width or bottom > height:
                    valid = False
                    break
                for r in range(top, bottom):
                    for c in range(left, right):
                        if (r, c) in covered or ((r, c) != (row_index, col_index) and cells[r][c] is not None):
                            valid = False
                            break
                        covered.add((r, c))
                    if not valid:
                        break
                if not valid:
                    break
                if right > left + 1 or bottom > top + 1:
                    merges.append({"start_row": top, "start_col": left, "end_row": bottom - 1, "end_col": right - 1})
            if not valid:
                break
        if not valid or len(covered) != height * width:
            continue
        try:
            extracted = table.extract()
        except Exception as exc:
            logger.info("PDF vector table text unavailable: error_type=%s", type(exc).__name__)
            continue
        if len(extracted) != height or any(len(row) != width for row in extracted):
            continue
        candidates.append({"rows": extracted, "merges": merges})
    return candidates


def _geometry_text_matches(rows: list[list[str]], native: list[list[str | None]]) -> bool:
    """Require multiple independent original-PDF text witnesses for alignment."""
    if len(rows) != len(native) or any(len(row) != len(source) for row, source in zip(rows, native, strict=True)):
        return False
    matched: list[tuple[int, int]] = []
    for row_index, (row, source) in enumerate(zip(rows, native, strict=True)):
        for col_index, (cell, original) in enumerate(zip(row, source, strict=True)):
            original_text = "".join(str(original or "").split()).casefold()
            if not original_text:
                continue
            if not isinstance(cell, str) or "".join(cell.split()).casefold() != original_text:
                return False
            matched.append((row_index, col_index))
    return len(matched) >= 3 and len({r for r, _ in matched}) >= 2 and len({c for _, c in matched}) >= 2


def _reconcile_physical_merges(payload: Any, native_tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Replace conflicting model merges only if one real PDF grid proves them wrong."""
    if not native_tables or not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
        return None
    revised = list(payload["tables"])
    repaired = 0
    for index, table in enumerate(payload["tables"]):
        if not isinstance(table, dict):
            return None
        try:
            _validate_page({"tables": [table], "paragraphs": [], "seals": []})
        except ValueError as exc:
            if not str(exc).startswith("Invalid Responses merge covers nonempty evidence"):
                return None
            matches = [item for item in native_tables if _geometry_text_matches(table["rows"], item["rows"])]
            if len(matches) != 1:
                return None
            candidate = {**table, "merges": matches[0]["merges"]}
            try:
                _validate_page({"tables": [candidate], "paragraphs": [], "seals": []})
            except ValueError:
                return None
            revised[index] = candidate
            repaired += 1
    return {**payload, "tables": revised} if repaired else None


def _read_page(image: bytes, page_number: int, page_count: int) -> dict[str, Any]:
    logger.info("Responses vision page %d/%d starting", page_number, page_count)
    try:
        payload = call_llm(
            image_paths=image,
            prompt=PAGE_PROMPT,
            retry_count=1,
            json_mode=True,
            response_schema=PAGE_SCHEMA,
        )
    except ResponsesEvidenceError as exc:
        raise ResponsesEvidenceError(exc.code, f"Responses page {page_number}/{page_count}: {exc}") from exc
    try:
        result = _validate_page(payload)
    except ValueError as exc:
        raise ResponsesEvidenceError("responses_invalid_schema", f"Responses page {page_number}/{page_count}: {exc}") from exc
    logger.info("Responses vision page %d/%d complete", page_number, page_count)
    return result


async def _read_page_async(image: bytes, page_number: int, page_count: int, native_tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Use the same strict validator on cancellable shared-loop requests."""
    logger.info("Responses vision page %d/%d starting", page_number, page_count)
    try:
        payload = await call_llm_async(
            image_paths=image,
            prompt=PAGE_PROMPT,
            retry_count=1,
            json_mode=True,
            response_schema=PAGE_SCHEMA,
        )
    except ResponsesEvidenceError as exc:
        raise ResponsesEvidenceError(exc.code, f"Responses page {page_number}/{page_count}: {exc}") from exc
    try:
        result = _validate_page(payload)
    except ValueError as exc:
        # A model's alleged merge must not destroy a different nonempty cell.
        # Recover only when the original PDF's drawn cell grid and independent
        # text witnesses uniquely prove the correct merge ranges.
        repaired = _reconcile_physical_merges(payload, native_tables)
        if repaired is None:
            raise ResponsesEvidenceError("responses_invalid_schema", f"Responses page {page_number}/{page_count}: {exc}") from exc
        result = _validate_page(repaired)
        logger.info("Responses page %d/%d repaired from verified PDF vector geometry", page_number, page_count)
    logger.info("Responses vision page %d/%d complete", page_number, page_count)
    return result


async def _read_batch(images: list[tuple[int, bytes, list[dict[str, Any]]]], page_count: int) -> list[dict[str, Any]]:
    """Cancel sibling provider I/O when any page's evidence fails."""
    tasks = [asyncio.create_task(_read_page_async(image, index, page_count, tables)) for index, image, tables in images]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception() is not None:
                raise task.exception()
        return [task.result() for task in tasks]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        # Drain cancellation before returning: no detached request may race
        # with another conversion or its cache publication.
        await asyncio.gather(*tasks, return_exceptions=True)


def _run_batch_sync(images: list[tuple[int, bytes, list[dict[str, Any]]]], page_count: int) -> list[dict[str, Any]]:
    operation = _read_batch(images, page_count)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(operation)
    # Async audit agents can call this synchronous API; nest no event loops.
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(asyncio.run, operation).result()


class VisionLayoutService:
    """Render on the PDF owner; make at most two concurrent Responses calls."""

    def analyze_full_pdf(self, file_path: str) -> list[dict[str, Any]]:
        if os.path.getsize(file_path) > parse_file_size(get_settings().MAX_FILE_SIZE):
            raise ValueError("PDF exceeds MAX_FILE_SIZE")
        with fitz.open(file_path) as document:
            page_count = len(document)
            if not page_count:
                raise ValueError("PDF contains no pages")
            results: list[dict[str, Any]] = []
            for first in range(0, page_count, 2):
                # PDF rendering and geometry extraction stay on the owning
                # thread; only bytes and plain Python data enter async tasks.
                images: list[tuple[int, bytes, list[dict[str, Any]]]] = []
                for index in range(first, min(first + 2, page_count)):
                    page = document[index]
                    native_tables = _native_table_candidates(page)
                    image = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")
                    images.append((index + 1, image, native_tables))
                results.extend(_run_batch_sync(images, page_count))
            return results
