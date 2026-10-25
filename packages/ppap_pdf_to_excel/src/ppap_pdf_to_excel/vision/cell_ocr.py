"""Transcribe verified raster cells without delegating cell topology to AI."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Any

from .client import ResponsesEvidenceError, call_llm_async
from .raster import RasterGrid, make_cell_panel

logger = logging.getLogger(__name__)

MAX_RASTER_EVIDENCE_SECONDS = 180
CELLS_PER_REQUEST = 64
CELL_SCHEMA: dict[str, Any] = {
    "name": "ppap_raster_cell_evidence",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cells": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
            },
        },
        "required": ["cells"],
    },
}

CELL_PROMPT = (
    "This image is a packed sheet of physically cropped table cells from a "
    "scanned document. Its packing order is not the document layout. "
    "Each crop has a printed CELL ID label OUTSIDE its original "
    "contents. For every requested ID, transcribe ONLY legible text inside "
    "that cell's crop, preserving all characters and reading order. "
    "Return exactly one object per requested integer ID. For a genuinely "
    "blank or unreadable cell, return an empty string. Never transcribe "
    "the CELL ID labels; do not infer omitted values, copy adjacent cells, "
    "decide whether cells are merged, or report PASS/NG. "
    "No text may come from outside the individually labelled crop. "
)


async def _read_grid(png: bytes, grid: RasterGrid) -> dict[str, Any]:
    values: dict[int, str] = {}

    for offset in range(0, len(grid.cells), CELLS_PER_REQUEST):
        requested = grid.cells[offset : offset + CELLS_PER_REQUEST]
        expected = {cell.index for cell in requested}
        panel = make_cell_panel(png, requested)
        started = monotonic()
        try:
            result = await call_llm_async(
                image_paths=panel,
                prompt=CELL_PROMPT + "Requested CELL IDs: " + ", ".join(str(cell.index) for cell in requested) + ".",
                retry_count=1,
                json_mode=True,
                response_schema=CELL_SCHEMA,
            )
        except BaseException as exc:
            logger.warning(
                "Raster OCR batch stopped: cells=%d png_bytes=%d elapsed_ms=%d error_type=%s",
                len(requested),
                len(panel),
                round((monotonic() - started) * 1000),
                type(exc).__name__,
            )
            raise
        entries = result.get("cells") if isinstance(result, dict) else None
        if not isinstance(entries, list) or len(entries) != len(expected):
            raise ResponsesEvidenceError("raster_cells_invalid_schema", "Raster OCR omitted or duplicated cell evidence")
        seen: set[int] = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "text"}:
                raise ResponsesEvidenceError("raster_cells_invalid_schema", "Raster OCR returned unsupported cell fields")
            index, text = entry["id"], entry["text"]
            if type(index) is not int or index not in expected or index in seen:
                raise ResponsesEvidenceError("raster_cells_invalid_schema", "Raster OCR returned invalid cell identity")
            if not isinstance(text, str) or len(text) > 32767:
                raise ResponsesEvidenceError("raster_cells_invalid_schema", "Raster OCR returned invalid cell text")
            seen.add(index)
            values[index] = text
        if seen != expected:
            raise ResponsesEvidenceError("raster_cells_invalid_schema", "Raster OCR returned incomplete cell identities")

    rows = [["" for _col in range(grid.col_count)] for _row in range(grid.row_count)]
    for cell in grid.cells:
        rows[cell.row][cell.col] = values[cell.index]
    return {
        "rows": rows,
        "merges": [{"start_row": r0, "start_col": c0, "end_row": r1, "end_col": c1} for r0, c0, r1, c1 in grid.merges],
    }


async def read_scan_tables(png: bytes, grids: list[RasterGrid]) -> list[dict[str, Any]]:
    """Transcribe every physical cell; the model never supplies table topology.

    Complete, non-overlapping pixel partitions are the source of row/column
    identity. Do not first generate a second, potentially malformed model grid
    and then compare two model transcriptions as if one were source truth.
    The caller owns the whole page deadline, including its remainder request.
    """
    built = []
    for index, grid in enumerate(grids, 1):
        built.append(await _read_grid(png, grid))
        logger.info(
            "Raster source grid verified: table=%d physical_rows=%d physical_cols=%d cells=%d merges=%d",
            index,
            grid.row_count,
            grid.col_count,
            len(grid.cells),
            len(grid.merges),
        )
    return built
