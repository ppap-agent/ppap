"""Original-PDF coordinates and unresolved visuals must survive the research reader."""

import hashlib
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from audit.document_tools import ReadLimits, effective
from documents.table.models import CellRegion, TableGrid
from PIL import Image

SPEC = importlib.util.spec_from_file_location("research_inspect_pdf", Path(__file__).with_name("inspect_pdf.py"))
assert SPEC is not None and SPEC.loader is not None
reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reader
SPEC.loader.exec_module(reader)


def original_pdf(path, *, extra_blank_page=False, rotation=0, embedded_image=False):
    with fitz.open() as document:
        page = document.new_page(width=300, height=300)
        page.insert_text((40, 25), "Original title")
        for y in (50, 100, 150):
            page.draw_line((30, y), (270, y))
        for x in (30, 150, 270):
            page.draw_line((x, 50), (x, 150))
        page.insert_text((40, 75), "Header")
        page.insert_text((160, 75), "Value")
        page.insert_text((40, 125), "Source row")
        page.insert_text((160, 125), "10.25")
        if embedded_image:
            image = BytesIO()
            Image.new("RGB", (10, 10), "black").save(image, format="PNG")
            page.insert_image(fitz.Rect(160, 110, 180, 135), stream=image.getvalue())
        page.set_rotation(rotation)
        if extra_blank_page:
            document.new_page(width=300, height=300)
        document.save(path)
    return path.read_bytes()


def test_real_pdf_grid_keeps_original_hash_coordinates_and_render(tmp_path):
    source = tmp_path / "source.pdf"
    original = original_pdf(source)
    output = tmp_path / "read"
    result = reader.inspect_pdf(source, output)
    assert source.read_bytes() == original
    assert result["source"] == {"file": "source.pdf", "format": ".pdf", "sha256": hashlib.sha256(original).hexdigest()}
    table = next(table for table in result["tables"] if table["id"] == "page-1")
    assert (table["rows"], table["cols"]) == (2, 2)
    assert table["cells"]["B2"]["value"] == "10.25"
    assert table["cells"]["B2"]["source"] == {"page": 1, "bbox": [150, 100, 270, 150]}
    assert table["cells"]["B2"]["state"] == "known"
    assert any(table["id"] == "page-1-outer-text" and table["cells"]["A1"]["value"] == "Original title" for table in result["tables"])
    assert result["coverage"] == {"page_count": 1, "grid_pages": [1], "unresolved_pages": []}
    assert json.loads((output / "snapshot.json").read_text()) == result
    assert Path(result["pages"][0]["render"]).read_bytes().startswith(b"\x89PNG")
    assert result["media"][0]["content_observed"] is False


def test_merge_aliases_uncovered_slots_and_image_overlap_remain_distinct():
    observed = SimpleNamespace(
        page_index=2,
        table_grid=TableGrid(
            rows=[10, 20, 30],
            cols=[40, 50, 60],
            cells=[CellRegion(0, 0, 0, 1, "merged native"), CellRegion(1, 1, 0, 0, "")],
        ),
    )
    table = reader._page_table(observed, [{"bbox": [42, 22, 48, 28]}])
    assert table["id"] == "page-3"
    assert table["merges"] == [[1, 1, 1, 2]]
    assert "B1" not in table["cells"]
    assert effective(table, 1, 2)["value"] == "merged native"
    assert effective(table, 1, 2)["source"] == {"page": 3, "bbox": [40, 10, 60, 20]}
    assert effective(table, 2, 1)["state"] == "unknown_visual_evidence"
    assert effective(table, 2, 2)["state"] == "unknown_uncovered_grid"


def test_embedded_image_keeps_native_value_unknown_and_points_to_render(tmp_path):
    source = tmp_path / "visual.pdf"
    original_pdf(source, embedded_image=True)
    result = reader.inspect_pdf(source, tmp_path / "read")
    table = result["tables"][0]
    assert table["cells"]["B2"]["value"] == "10.25"
    assert table["cells"]["B2"]["state"] == "unknown_visual_evidence"
    anchor = result["drawing_anchors"][0]
    assert anchor["sheet"] == "page-1"
    assert anchor["from"] == anchor["to"] == {"row": 2, "column": 2}
    assert anchor["media_paths"] == ["page-1.png"]
    assert anchor["content_observed"] is False


def test_page_without_grid_is_retained_as_unresolved(tmp_path):
    source = tmp_path / "partial.pdf"
    original_pdf(source, extra_blank_page=True)
    result = reader.inspect_pdf(source, tmp_path / "read")
    assert result["coverage"] == {"page_count": 2, "grid_pages": [1], "unresolved_pages": [2]}
    assert [page["page"] for page in result["pages"]] == [1, 2]
    assert Path(result["pages"][1]["render"]).is_file()


def test_rotated_source_is_rejected_instead_of_emitting_false_coordinates(tmp_path):
    source = tmp_path / "rotated.pdf"
    original_pdf(source, rotation=90)
    with pytest.raises(ValueError, match="unrotated"):
        reader.inspect_pdf(source, tmp_path / "read")


def test_existing_output_and_source_limit_are_not_silently_reused(tmp_path):
    source = tmp_path / "source.pdf"
    original_pdf(source)
    with pytest.raises(ValueError, match="source bytes"):
        reader.inspect_pdf(source, tmp_path / "limited", limits=ReadLimits(source_bytes=1))
    output = tmp_path / "prior"
    output.mkdir()
    with pytest.raises(FileExistsError):
        reader.inspect_pdf(source, output)
