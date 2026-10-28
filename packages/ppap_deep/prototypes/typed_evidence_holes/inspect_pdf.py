"""Research-only PDF grids with original page coordinates; no OCR or model calls.

Sheet names are page-local addressing handles, not converted XLSX sources.
Rendering makes visual evidence available; it does not interpret its pixels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import fitz
from ppap_deep.semantic.document_tools import ReadLimits, effective, ref, within
from ppap_docops.pdf import PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC, GroupingMode, GroupingSpec, StitchMode, StitchSpec, extract_pdf_tables


def _intersects(first: list[float], second: list[float]) -> bool:
    return max(first[0], second[0]) < min(first[2], second[2]) and max(first[1], second[1]) < min(first[3], second[3])


def _page_table(page_result, images: list[dict]) -> dict:
    grid = page_result.table_grid
    number = page_result.page_index + 1
    table = {"id": f"page-{number}", "page": number, "rows": len(grid.rows) - 1, "cols": len(grid.cols) - 1, "cells": {}, "merges": [], "coordinate_system": "pdf_points_top_left"}
    # Uncovered grid slots are unknown, not native blanks. Every address has a
    # page/bbox even when the line reconstruction did not identify a cell.
    for row in range(table["rows"]):
        for column in range(table["cols"]):
            bbox = [grid.cols[column], grid.rows[row], grid.cols[column + 1], grid.rows[row + 1]]
            table["cells"][ref(row + 1, column + 1)] = {"value": None, "state": "unknown_uncovered_grid", "source": {"page": number, "bbox": bbox}}
    occupied: set[tuple[int, int]] = set()
    for cell in grid.cells:
        if not 0 <= cell.row_start <= cell.row_end < table["rows"] or not 0 <= cell.col_start <= cell.col_end < table["cols"]:
            raise ValueError(f"Invalid source cell span on page {number}")
        slots = {(row, column) for row in range(cell.row_start, cell.row_end + 1) for column in range(cell.col_start, cell.col_end + 1)}
        if occupied.intersection(slots):
            raise ValueError(f"Overlapping source cells on page {number}")
        occupied.update(slots)
        bbox = [grid.cols[cell.col_start], grid.rows[cell.row_start], grid.cols[cell.col_end + 1], grid.rows[cell.row_end + 1]]
        state = "unknown_visual_evidence" if any(_intersects(bbox, image["bbox"]) for image in images) else "known"
        anchor = ref(cell.row_start + 1, cell.col_start + 1)
        for row, column in slots:
            table["cells"].pop(ref(row + 1, column + 1))
        table["cells"][anchor] = {"value": cell.content, "state": state, "source": {"page": number, "bbox": bbox}}
        if len(slots) > 1:
            table["merges"].append([cell.row_start + 1, cell.col_start + 1, cell.row_end + 1, cell.col_end + 1])
    return table


def inspect_pdf(source: Path, out: Path, *, limits: ReadLimits | None = None) -> dict:
    """Keep every source page; reject unsupported coordinate transforms explicitly."""
    limits = limits or ReadLimits()
    source = source.resolve(strict=True)
    if source.suffix.lower() != ".pdf":
        raise ValueError("Expected an original PDF")
    within(source.stat().st_size, limits.source_bytes, "source bytes")
    raw = source.read_bytes()
    within(len(raw), limits.source_bytes, "source bytes")
    with fitz.open(stream=raw, filetype="pdf") as document:
        within(len(document), limits.pages, "pages")
        if not len(document):
            raise ValueError("PDF has no pages")
        if any(page.rotation or tuple(page.cropbox_position) != (0.0, 0.0) for page in document):
            raise ValueError("Research PDF coordinates require unrotated pages and a zero crop origin")
        spec = replace(
            PPAP_1_DIMENSION_MEASUREMENT_REPORT_SPEC,
            grouping=GroupingSpec(mode=GroupingMode.EXPLICIT_RANGES, explicit_ranges=tuple((index, index) for index in range(len(document)))),
            stitch=StitchSpec(mode=StitchMode.NO_STITCH),
        )
        extraction = extract_pdf_tables(raw, spec=spec)
        pages = {page.page_index: page for group in extraction.groups for page in group.page_results}
        result: dict[str, Any] = {
            "source": {"file": source.name, "sha256": hashlib.sha256(raw).hexdigest(), "format": ".pdf"},
            "reader": "doc01_page_linegrid_v1",
            "tables": [],
            "pages": [],
            "media": [],
            "drawing_anchors": [],
            "coverage": {"page_count": len(document), "grid_pages": [], "unresolved_pages": []},
            "limitations": [
                "Page-local A1 addresses identify extracted PDF geometry, not a source workbook. Source coordinates use PDF points, top-left origin; page numbers are one-based.",
                "Native text placement and line reconstruction are observations, not verified semantic bindings or complete visual interpretation.",
                "Cells intersecting embedded images and grid slots without a reconstructed cell remain unknown. Vector-only marks are not interpreted; inspect rendered pages when material.",
                "Text outside the grid is retained separately. Missing page grids remain unresolved and must not be omitted from scope.",
            ],
        }
        out.mkdir(parents=True, mode=0o700, exist_ok=False)
        rendered_bytes = cell_count = merge_count = 0
        for index, page in enumerate(document):
            number = index + 1
            image_path = out / f"page-{number}.png"
            scale = min(2.0, 2000 / max(page.rect.width, page.rect.height))
            png = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
            rendered_bytes += len(png)
            within(rendered_bytes, limits.expanded_bytes, "page render bytes")
            image_path.write_bytes(png)
            media_path = str(image_path.resolve())
            result["media"].append({"media_path": image_path.name, "extracted_path": media_path, "page": number, "sha256": hashlib.sha256(png).hexdigest(), "content_observed": False})
            images: list[dict] = [{"bbox": list(item["bbox"]), "content_observed": False} for item in page.get_image_info()]
            blocks = [{"bbox": list(block[:4]), "text": block[4]} for block in page.get_text("blocks") if block[6] == 0]
            result["pages"].append({"page": number, "width": page.rect.width, "height": page.rect.height, "render": media_path, "render_scale": scale, "blocks": blocks, "images": images})
            observed = pages.get(index)
            if observed is None or not observed.success or not observed.table_grid.cells:
                result["coverage"]["unresolved_pages"].append(number)
                continue
            cell_count += (len(observed.table_grid.rows) - 1) * (len(observed.table_grid.cols) - 1)
            merge_count += sum(cell.row_start != cell.row_end or cell.col_start != cell.col_end for cell in observed.table_grid.cells)
            within(cell_count, limits.cells, "cells")
            within(merge_count, limits.merges, "merges")
            table = _page_table(observed, images)
            result["tables"].append(table)
            result["coverage"]["grid_pages"].append(number)
            for image in images:
                cells = [(row, col) for row in range(1, table["rows"] + 1) for col in range(1, table["cols"] + 1) if _intersects(effective(table, row, col)["source"]["bbox"], image["bbox"])]
                if cells:
                    result["drawing_anchors"].append(
                        {
                            "sheet": table["id"],
                            "page": number,
                            "bbox": image["bbox"],
                            "media_paths": [image_path.name],
                            "content_observed": False,
                            "from": {"row": min(row for row, col in cells), "column": min(col for row, col in cells)},
                            "to": {"row": max(row for row, col in cells), "column": max(col for row, col in cells)},
                        }
                    )
            outer = observed.outer_text_lines
            if outer:
                result["tables"].append(
                    {
                        "id": f"page-{number}-outer-text",
                        "page": number,
                        "role": "native_text_outside_grid",
                        "rows": len(outer),
                        "cols": 1,
                        "merges": [],
                        "cells": {
                            ref(row, 1): {
                                "value": line.text,
                                "state": "unknown_visual_evidence" if any(_intersects([line.x0, line.y0, line.x1, line.y1], image["bbox"]) for image in images) else "known",
                                "source": {"page": number, "bbox": [line.x0, line.y0, line.x1, line.y1]},
                            }
                            for row, line in enumerate(outer, 1)
                        },
                    }
                )
        within(len(result["tables"]), limits.tables, "tables")
    encoded = json.dumps(result, ensure_ascii=False, indent=2).encode()
    within(len(encoded), limits.output_bytes, "snapshot bytes")
    (out / "snapshot.json").write_bytes(encoded)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_pdf(args.source, args.out)
    print(json.dumps({"snapshot": str((args.out / "snapshot.json").resolve()), "source_sha256": result["source"]["sha256"], "tables": len(result["tables"]), "coverage": result["coverage"]}))


if __name__ == "__main__":
    main()
