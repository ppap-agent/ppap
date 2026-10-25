"""Structured multimodal PDF-page reading through the Responses protocol."""

from __future__ import annotations

import asyncio
import logging
import os
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fitz
from ppap_core.settings import get_settings
from ppap_docops.files import parse_file_size

from .cell_ocr import recover_scan_page
from .client import ResponsesEvidenceError, call_llm_async
from .raster import detect_raster_grids

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
    """Build complete fine-grained cell grids from the PDF's drawn rectangles.

    This is deliberately independent of the language model's row/column count.
    A PDF with staggered header/body column boundaries can contain genuine
    physical spans even when the model flattens those rows differently.
    """
    try:
        tables = page.find_tables(strategy="lines").tables
    except Exception as exc:
        logger.warning("PDF source-grid extraction failed: error_type=%s", type(exc).__name__)
        return []

    candidates: list[dict[str, Any]] = []
    for table in tables:
        if not 0 < table.row_count <= 500 or not 0 < table.col_count <= 100:
            continue
        cells = [list(row.cells) for row in table.rows]
        if len(cells) != table.row_count or any(len(row) != table.col_count for row in cells):
            continue
        rectangles = [rect for row in cells for rect in row if rect is not None]
        if not rectangles:
            continue
        xs = _grid_bounds([float(rect[i]) for rect in rectangles for i in (0, 2)])
        ys = _grid_bounds([float(rect[i]) for rect in rectangles for i in (1, 3)])
        height, width = len(ys) - 1, len(xs) - 1
        if not 0 < height <= 500 or not 0 < width <= 100:
            continue
        try:
            extracted = table.extract()
        except Exception as exc:
            logger.info("PDF vector table text unavailable: error_type=%s", type(exc).__name__)
            continue
        if len(extracted) != len(cells) or any(len(row) != len(original) for row, original in zip(cells, extracted, strict=True)):
            continue

        # Rebuild from original rectangle coordinates, not from the table
        # finder's inferred column indices. Header and body can have distinct
        # subdivisions: the union of physical bounds is the common fine grid.
        rows = [["" for _ in range(width)] for _ in range(height)]
        merges: list[dict[str, int]] = []
        covered: set[tuple[int, int]] = set()
        valid = True
        for rectangles_row, text_row in zip(cells, extracted, strict=True):
            for rect, content in zip(rectangles_row, text_row, strict=True):
                if rect is None:
                    if content:
                        valid = False
                    continue
                left, top, right, bottom = (_boundary_index(float(rect[i]), axis) for i, axis in ((0, xs), (1, ys), (2, xs), (3, ys)))
                if left < 0 or top < 0 or not (left < right <= width) or not (top < bottom <= height):
                    valid = False
                    break
                for row_index in range(top, bottom):
                    for col_index in range(left, right):
                        if (row_index, col_index) in covered:
                            valid = False
                            break
                        covered.add((row_index, col_index))
                    if not valid:
                        break
                if not valid:
                    break
                if rows[top][left]:
                    valid = False
                    break
                rows[top][left] = str(content or "")
                if right - left > 1 or bottom - top > 1:
                    merges.append({"start_row": top, "start_col": left, "end_row": bottom - 1, "end_col": right - 1})
            if not valid:
                break
        if not valid or len(covered) != height * width:
            continue
        # Prohibit double-writing any independently positioned source text.
        try:
            _validate_page({"tables": [{"rows": rows, "merges": merges}], "paragraphs": [], "seals": []})
        except ValueError:
            continue
        candidates.append({"rows": rows, "merges": merges})

    if not candidates:
        # Safe metadata only; extraction APIs may themselves fail on bad PDFs.
        try:
            drawings_count = len(page.get_drawings())
            word_count = len(page.get_text("words"))
        except Exception:
            drawings_count = word_count = -1
        logger.warning("PDF source grid unavailable: found_tables=%d vector_drawings=%d text_words=%d", len(tables), drawings_count, word_count)
    return candidates


def _canonical_source_text(value: str | None) -> str:
    """Normalize transport whitespace only; never generate missing evidence."""
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _geometry_text_matches(rows: list[list[str]], native: list[list[str | None]]) -> bool:
    """Exact row/column mapping with every nonempty original text accounted for."""
    if len(rows) != len(native) or any(len(row) != len(source) for row, source in zip(rows, native, strict=True)):
        return False
    matched: list[tuple[int, int]] = []
    for row_index, (row, source) in enumerate(zip(rows, native, strict=True)):
        for col_index, (cell, original) in enumerate(zip(row, source, strict=True)):
            original_text = _canonical_source_text(original)
            if not original_text:
                continue
            if not isinstance(cell, str) or _canonical_source_text(cell) != original_text:
                return False
            matched.append((row_index, col_index))
    return len(matched) >= 3 and len({r for r, _ in matched}) >= 2 and len({c for _, c in matched}) >= 2


def _source_cell_alignment(model_rows: list[list[str]], native_rows: list[list[str | None]]) -> tuple[bool, str]:
    """Prove text survival across a flattened/model-resegmented native PDF grid.

    A source cell can legitimately contain multiple textual fragments that the
    model incorrectly placed into distinct rows/columns. Every nonempty model
    fragment must have exactly one source-PDF cell as a witness; otherwise we
    cannot replace its model grid without risking a document-evidence loss.
    """
    if not model_rows or not native_rows:
        return False, "empty_grid"
    source = [(row, col, _canonical_source_text(value)) for row, cells in enumerate(native_rows) for col, value in enumerate(cells) if _canonical_source_text(value)]
    model = [_canonical_source_text(value) for row in model_rows for value in row if isinstance(value, str) and _canonical_source_text(value)]
    if len(source) < 4 or len(model) < 4:
        return False, "insufficient_witnesses"

    matched: set[tuple[int, int]] = set()
    for value in model:
        # Fragment matches are permitted only within a longer source-PDF text
        # cell, and not for very short ambiguous labels such as 'SC' or '产品'.
        occurrences = [(row, col) for row, col, candidate in source if candidate == value or (len(value) >= 4 and value in candidate)]
        if len(occurrences) != 1:
            return False, "missing_or_ambiguous_source_text"
        matched.add(occurrences[0])

    if len(matched) < 4 or len({r for r, _ in matched}) < 2 or len({c for _, c in matched}) < 2:
        return False, "insufficient_independent_coordinates"
    return True, "source_cell_text_proven"


def _reconcile_physical_merges(payload: Any, native_tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Use uniquely attested native cell geometry, never a guessed model span."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
        return None
    if not native_tables:
        logger.warning("Responses geometry repair unavailable: reason=no_native_vector_grid, model_tables=%d", len(payload["tables"]))
        return None

    revised = list(payload["tables"])
    repaired = 0
    for index, table in enumerate(payload["tables"]):
        if not isinstance(table, dict) or not isinstance(table.get("rows"), list):
            return None
        try:
            _validate_page({"tables": [table], "paragraphs": [], "seals": []})
        except ValueError as exc:
            if not str(exc).startswith("Invalid Responses merge covers nonempty evidence"):
                logger.warning("Responses geometry repair refused: reason=non_merge_schema_error, table=%d", index + 1)
                return None

            strict = [item for item in native_tables if _geometry_text_matches(table["rows"], item["rows"])]
            if len(strict) > 1:
                logger.warning("Responses geometry repair refused: reason=multiple_exact_source_grids table=%d", index + 1)
                return None
            if len(strict) == 1:
                # Same grid: retain every model cell; only physical merge ranges
                # are authoritative.
                candidate = {**table, "merges": strict[0]["merges"]}
                alignment = "same_grid"
            else:
                # Different row/column segmentation: use the actual original-PDF
                # text and cell geometry only if all model evidence survives.
                evaluations = [(item, *_source_cell_alignment(table["rows"], item["rows"])) for item in native_tables]
                verified = [item for item, accepted, _reason in evaluations if accepted]
                if len(verified) != 1:
                    reasons = sorted({reason for _item, accepted, reason in evaluations if not accepted})
                    if len(verified) > 1:
                        reasons.append("multiple_native_grids")
                    logger.warning(
                        "Responses geometry repair refused: reasons=%s table=%d native_candidates=%d model_rows=%d source_shapes=%s",
                        reasons,
                        index + 1,
                        len(native_tables),
                        len(table["rows"]),
                        [(len(item["rows"]), max((len(row) for row in item["rows"]), default=0)) for item in native_tables[:6]],
                    )
                    return None
                native = verified[0]
                candidate = {**table, "rows": [[str(value or "") for value in row] for row in native["rows"]], "merges": native["merges"]}
                alignment = "source_grid"

            try:
                _validate_page({"tables": [candidate], "paragraphs": [], "seals": []})
            except ValueError as invalid:
                logger.warning("Responses geometry repair refused: reason=source_grid_validation_failed error_type=%s table=%d", type(invalid).__name__, index + 1)
                return None
            revised[index] = candidate
            repaired += 1
            logger.info("Responses geometry attested: table=%d mode=%s", index + 1, alignment)

    return {**payload, "tables": revised} if repaired else None


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
        vectors = [candidate for candidate in native_tables if "rows" in candidate]
        raster_grids = [candidate["raster_grid"] for candidate in native_tables if "raster_grid" in candidate]
        repaired = _reconcile_physical_merges(payload, vectors) if vectors else None
        if repaired is None and raster_grids and str(exc).startswith("Invalid Responses merge covers nonempty evidence"):
            try:
                repaired = await recover_scan_page(image, payload, raster_grids)
            except ResponsesEvidenceError as recovery_error:
                raise ResponsesEvidenceError(
                    recovery_error.code,
                    f"Responses page {page_number}/{page_count}: {recovery_error}",
                ) from recovery_error
        if repaired is None:
            raise ResponsesEvidenceError(
                "responses_invalid_schema", f"Responses page {page_number}/{page_count}: {exc}"
            ) from exc
        result = _validate_page(repaired)
        logger.info("Responses page %d/%d recovered from source-grounded table evidence", page_number, page_count)
    logger.info("Responses vision page %d/%d complete", page_number, page_count)
    return result


async def _read_batch(images: list[tuple[int, bytes, list[dict[str, Any]]]], page_count: int) -> list[dict[str, Any]]:
    """Cancel sibling provider I/O when any page's evidence fails."""
    tasks = [asyncio.create_task(_read_page_async(image, index, page_count, tables)) for index, image, tables in images]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if not task.done() or task.cancelled():
                continue
            failure = task.exception()
            if failure is not None:
                raise failure
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
                    if not native_tables:
                        try:
                            raster_grids = detect_raster_grids(image)
                        except ValueError as exc:
                            logger.warning("Raster image source unavailable: error_type=%s", type(exc).__name__)
                            raster_grids = []
                        native_tables.extend({"raster_grid": grid} for grid in raster_grids)
                    images.append((index + 1, image, native_tables))
                results.extend(_run_batch_sync(images, page_count))
            return results
