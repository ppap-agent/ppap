"""Structured multimodal PDF-page reading through the Responses protocol."""

from __future__ import annotations

import logging
import os
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import fitz
from ppap_core.settings import get_settings
from ppap_docops.files import parse_file_size

from .client import ResponsesEvidenceError, call_llm

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


class VisionLayoutService:
    """Render safely in the owning thread; overlap only bounded HTTP I/O."""

    def analyze_full_pdf(self, file_path: str) -> list[dict[str, Any]]:
        if os.path.getsize(file_path) > parse_file_size(get_settings().MAX_FILE_SIZE):
            raise ValueError("PDF exceeds MAX_FILE_SIZE")
        with fitz.open(file_path) as document:
            page_count = len(document)
            if not page_count:
                raise ValueError("PDF contains no pages")
            results: list[dict[str, Any] | None] = [None] * page_count
            # Bound in-flight images and API requests; rendering is not shared across threads.
            pending: deque[tuple[int, Future[dict[str, Any]]]] = deque()
            with ThreadPoolExecutor(max_workers=min(2, page_count)) as executor:
                for index in range(page_count):
                    pix = document[index].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    pending.append((index, executor.submit(_read_page, pix.tobytes("png"), index + 1, page_count)))
                    if len(pending) >= 2:
                        completed_index, future = pending.popleft()
                        results[completed_index] = future.result()
                while pending:
                    completed_index, future = pending.popleft()
                    results[completed_index] = future.result()
            if any(page is None for page in results):
                raise RuntimeError("Missing Responses page result")
            return [page for page in results if page is not None]
