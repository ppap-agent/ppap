"""Canonical, source-preserving PaddleOCR evidence for local comparison."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .client import require_pages
from .layout import page_layouts
from .seals import seal_evidence
from .tables import recognize_tables


def build_evidence(result: dict, *, page_count: int, pdf_path: str | Path | None = None) -> dict:
    require_pages(result, page_count)
    layouts = page_layouts(result, page_count)
    tables = recognize_tables(pdf_path, result, layouts)
    pages = []
    for page_index, layout in enumerate(layouts):
        pruned = result["layoutParsingResults"][page_index]["prunedResult"]
        page_tables = [table for table in tables if table["page"] == page_index + 1]
        pages.append({
            "page": page_index + 1,
            "regions": layout["regions"],
            "texts": layout["texts"],
            "seals": seal_evidence(pruned, layout["regions"], page_index + 1),
            "tables": page_tables,
        })
    return {
        "schema_version": "paddle_layout_evidence_v1",
        "engine": "paddleocr_ppstructure_v3",
        "source_sha256": hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest() if pdf_path is not None else None,
        "pages": pages,
    }
