"""Original PDF grids retain source coordinates, coverage and explicit unknowns."""

import hashlib
import json
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
from audit import document_tools
from audit.document_tools import ParserLimitError, ReadLimits, effective, snapshot
from audit.validation import validate_snapshot
from documents.pdf import PdfConversionError
from documents.table.models import CellRegion, TableGrid
from PIL import Image


def original_pdf(tmp_path, *, extra_page=None, rotation=0, crop=False, images=False):
    path = tmp_path / "original.pdf"
    with fitz.open() as document:
        page = document.new_page(width=300, height=300)
        page.insert_text((40, 25), "Original title")
        for y in (50, 100, 150, 200):
            page.draw_line((30, y), (270, y))
        for x in (30, 150, 270):
            page.draw_line((x, 50), (x, 200))
        for point, text in (((40, 75), "Header"), ((160, 75), "Value"), ((40, 125), "Source row"), ((160, 125), "10.25"), ((40, 175), "Image row")):
            page.insert_text(point, text)
        if images:
            picture = BytesIO()
            Image.new("RGB", (10, 10), "black").save(picture, format="PNG")
            for top in (110, 160):
                page.insert_image(fitz.Rect(160, top, 180, top + 25), stream=picture.getvalue())
        if crop:
            page.set_cropbox(fitz.Rect(10, 10, 290, 290))
        page.set_rotation(rotation)
        if extra_page == "grid":
            document.fullcopy_page(0)
        elif extra_page == "blank":
            document.new_page(width=300, height=300)
        document.save(path)
    return path


def checked(path, **kwargs):
    data = snapshot(path, **kwargs)
    validate_snapshot(data, hashlib.sha256(path.read_bytes()).hexdigest(), ".pdf")
    assert json.loads(json.dumps(data, allow_nan=False)) == data
    return data


def native_result(monkeypatch, grid):
    page = SimpleNamespace(page_index=0, success=True, table_grid=grid)
    result = SimpleNamespace(groups=[SimpleNamespace(page_results=[page])])
    monkeypatch.setattr("documents.pdf.extract_pdf_tables", lambda *args, **kwargs: result)


def test_pdf_native_grid_and_original_text_blocks_keep_their_own_identity(tmp_path):
    path = original_pdf(tmp_path, extra_page="blank")
    before = path.read_bytes()
    data = checked(path)
    assert path.read_bytes() == before
    assert data["source"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert [table["id"] for table in data["tables"]] == ["page-1"]
    assert [page["page"] for page in data["pages"]] == [1, 2]
    assert "Original title" in data["pages"][0]["text"]
    assert any("Original title" in block["text"] for block in data["pages"][0]["blocks"])
    table = data["tables"][0]
    assert (table["page"], table["rows"], table["cols"]) == (1, 3, 2)
    assert table["cells"]["B2"] == {"value": "10.25", "state": "known", "source": {"page": 1, "bbox": [150, 100, 270, 150]}}
    assert table["cells"]["B3"]["state"] == "known"
    assert not table["cells"]["B3"]["value"]
    assert data["coverage"] == {"page_count": 2, "grid_pages": [1], "unresolved_pages": [2], "unresolved_reasons": {"2": "native_grid_unavailable"}}


def test_native_text_survives_image_overlap_but_image_only_content_stays_unknown(tmp_path):
    data = checked(original_pdf(tmp_path, images=True))
    cells = data["tables"][0]["cells"]
    assert (cells["B2"]["value"], cells["B2"]["state"], cells["B2"]["image_overlap"]) == ("10.25", "unknown_visual_evidence", True)
    assert (cells["B3"]["state"], cells["B3"]["image_overlap"]) == ("unknown_visual_evidence", True)


def vector_pdf(tmp_path, mark=None, *, native_text=False):
    path = tmp_path / "vectors.pdf"
    with fitz.open() as document:
        page = document.new_page(width=280, height=120)
        if mark == "cell_background":
            page.draw_rect(fitz.Rect(20, 60, 140, 100), fill=(1, 1, 0))
        elif mark == "table_background":
            page.draw_rect(fitz.Rect(20, 20, 260, 100), fill=(0, 0, 0))
        elif mark in {"adjacent_backgrounds", "expanded_backgrounds"}:
            for left in (20, 140):
                for top in (20, 60):
                    bounds = [left - 0.12, top - 0.12, left + 120.12, top + 40.12]
                    if mark == "adjacent_backgrounds":
                        bounds = [max(20, bounds[0]), max(20, bounds[1]), min(260, bounds[2]), min(100, bounds[3])]
                    page.draw_rect(fitz.Rect(bounds), color=None, fill=(1, 1, 0))
        for x in (20, 140, 260):
            if mark == "thin_rules":
                page.draw_rect(fitz.Rect(x - 0.36, 20, x + 0.36, 100), color=None, fill=(0, 0, 0))
            else:
                page.draw_line((x, 20), (x, 100))
        for y in (20, 60, 100):
            if mark == "thin_rules":
                page.draw_rect(fitz.Rect(20, y - 0.36, 260, y + 0.36), color=None, fill=(0, 0, 0))
            else:
                page.draw_line((20, y), (260, y))
        if mark == "circle":
            page.draw_circle((80, 80), 8, fill=(0, 0, 0))
        elif mark == "diagonal":
            page.draw_line((20, 60), (140, 100))
        elif mark == "near_edge_short_line":
            page.draw_line((60, 60.5), (100, 60.5))
        elif mark == "near_edge_short_bar":
            page.draw_rect(fitz.Rect(60, 60.2, 100, 60.8), color=None, fill=(0, 0, 0))
        elif mark == "near_edge_full_line":
            page.draw_line((20.5, 60.5), (139.5, 60.5))
        elif mark == "corner_short_bar":
            page.draw_rect(fitz.Rect(19.8, 59.8, 40, 60.8), color=None, fill=(0, 0, 0))
        if native_text:
            page.insert_text((35, 85), "Native")
        document.save(path)
    return path


@pytest.mark.parametrize("mark", ["circle", "diagonal", "near_edge_short_line", "near_edge_short_bar", "near_edge_full_line"])
def test_uninterpreted_vector_mark_cannot_be_known_blank(tmp_path, mark):
    data = checked(vector_pdf(tmp_path, mark))
    table = data["tables"][0]
    assert (table["rows"], table["cols"]) == (2, 2)
    cell = table["cells"]["A2"]
    assert cell["value"] == ""
    assert cell["state"] == "unknown_visual_evidence"
    assert cell["visual_overlap"] is True
    assert cell["vector_path_indices"] == [7]
    assert table["vector_path_count"] == 7
    assert all(table["cells"][address]["state"] == "known" for address in ("B1", "B2"))
    # The parser places the nearby short bar across its reconstructed boundary;
    # both affected cells must then remain unknown.
    assert table["cells"]["A1"]["state"] == ("unknown_visual_evidence" if mark == "near_edge_short_bar" else "known")
    cell["state"] = "known"
    with pytest.raises(ValueError, match="uninterpreted visual"):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")


@pytest.mark.parametrize("mark", [None, "cell_background", "table_background", "thin_rules", "adjacent_backgrounds"])
def test_pure_grid_and_whole_cell_rectangle_backgrounds_preserve_blank_observation(tmp_path, mark):
    table = checked(vector_pdf(tmp_path, mark))["tables"][0]
    assert (table["rows"], table["cols"]) == (2, 2)
    assert all(cell["state"] == "known" and not cell["value"] and not cell.get("visual_overlap") for cell in table["cells"].values())


def test_partial_bar_crossing_grid_corner_is_not_a_full_edge_rule(tmp_path):
    table = checked(vector_pdf(tmp_path, "corner_short_bar"))["tables"][0]
    cell = table["cells"]["A2"]
    assert (table["rows"], table["cols"]) == (2, 3)
    assert cell["state"] == "unknown_visual_evidence"
    assert cell["vector_path_indices"] == [7]
    assert [2, 1, 2, 2] in table["merges"]


@pytest.mark.xfail(strict=True, reason="Known parser-boundary false positive: expanded background shifts grid bounds beyond original rule endpoints")
def test_expanded_outer_grid_backgrounds_should_preserve_known_blank(tmp_path):
    # Background edges move the reconstructed outer bounds by 0.1 pt. The
    # original rules no longer span those full bounds. These are known false
    # positives. Keep the correct known-blank expectation open rather than
    # treating the current conservative result as successful interpretation.
    table = checked(vector_pdf(tmp_path, "expanded_backgrounds"))["tables"][0]
    assert table["cells"]["A1"]["source"]["bbox"] == [19.9, 19.9, 140.0, 60.0]
    assert all(cell["state"] == "known" and not cell["value"] and not cell.get("visual_overlap") for cell in table["cells"].values())


def test_native_text_remains_observed_when_uninterpreted_vector_overlaps(tmp_path):
    cell = checked(vector_pdf(tmp_path, "circle", native_text=True))["tables"][0]["cells"]["A2"]
    assert (cell["value"], cell["state"], cell["visual_overlap"]) == ("Native", "known", True)
    assert cell["native_text_unobscured"] is True
    assert cell["vector_path_indices"] == [7]


@pytest.mark.parametrize("failure", ["path_count", "path_index", "missing_paths", "missing_overlap"])
def test_vector_observation_requires_original_path_references(tmp_path, failure):
    data = checked(vector_pdf(tmp_path, "circle"))
    table = data["tables"][0]
    cell = table["cells"]["A2"]
    if failure == "path_count":
        table["vector_path_count"] = -1
    elif failure == "path_index":
        cell["vector_path_indices"] = [8]
    elif failure == "missing_paths":
        del cell["vector_path_indices"]
    else:
        del cell["visual_overlap"]
    with pytest.raises(ValueError):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")


@pytest.mark.parametrize("rotation,crop", [(90, False), (0, True)])
def test_unsupported_page_transform_preserves_blocks_without_claiming_grid(tmp_path, rotation, crop):
    data = checked(original_pdf(tmp_path, rotation=rotation, crop=crop))
    assert data["tables"] == []
    assert data["pages"][0]["blocks"]
    assert data["coverage"]["unresolved_reasons"] == {"1": "unsupported_page_transform"}


def test_merge_anchors_uncovered_slots_and_bad_geometry_are_distinct(tmp_path, monkeypatch):
    native_result(
        monkeypatch,
        TableGrid(rows=[10, 20, 30], cols=[-10, 40, 60], cells=[CellRegion(0, 0, 0, 0, "outside"), CellRegion(0, 0, 1, 1, "inside"), CellRegion(1, 1, 0, 1, "spanned")]),
    )
    table = checked(original_pdf(tmp_path))["tables"][0]
    assert table["cells"]["A1"]["state"] == "unknown_geometry"
    assert table["cells"]["A1"]["source"]["bbox"] == [-10, 10, 40, 20]
    assert table["cells"]["B1"]["state"] == "known"
    assert table["merges"] == [[2, 1, 2, 2]]
    assert "B2" not in table["cells"]
    assert effective(table, 2, 2)["source"] == {"page": 1, "bbox": [-10, 20, 60, 30]}
    assert effective(table, 2, 2)["state"] == "unknown_geometry"
    native_result(monkeypatch, TableGrid(rows=[10, 20, 30], cols=[40, 50, 60], cells=[CellRegion(0, 0, 0, 1, "merged"), CellRegion(1, 1, 0, 0, "")]))
    table = checked(original_pdf(tmp_path))["tables"][0]
    assert table["cells"]["B2"] == {"value": None, "state": "unknown_uncovered_grid", "source": {"page": 1, "bbox": [50, 20, 60, 30]}}
    assert effective(table, 1, 2)["source"]["bbox"] == [40, 10, 60, 20]


@pytest.mark.parametrize("axis", [[10, float("nan"), 30], [10, 30, 20]])
def test_nonfinite_or_nonmonotonic_grid_retains_page_as_unresolved(tmp_path, monkeypatch, axis):
    native_result(monkeypatch, TableGrid(rows=axis, cols=[10, 20], cells=[CellRegion(0, 0, 0, 0, "x")]))
    data = checked(original_pdf(tmp_path))
    assert data["tables"] == []
    assert data["coverage"]["unresolved_reasons"] == {"1": "invalid_grid_boundaries"}


@pytest.mark.parametrize("failure", ["source", "page", "bounds", "overlap", "omitted_cell", "boundary", "coverage", "transform"])
def test_snapshot_validation_rejects_false_pdf_geometry_and_coverage(tmp_path, failure):
    data = checked(original_pdf(tmp_path))
    table = data["tables"][0]
    if failure == "source":
        del table["cells"]["B2"]["source"]
    elif failure == "page":
        table["cells"]["B2"]["source"]["page"] = 2
    elif failure == "bounds":
        table["cells"]["B2"]["source"]["bbox"][2] = 301
    elif failure == "overlap":
        table["merges"] = [[1, 1, 1, 2]]
    elif failure == "omitted_cell":
        del table["cells"]["B2"]
    elif failure == "boundary":
        table["cells"]["B2"]["source"]["bbox"][0] = 149
    elif failure == "coverage":
        data["coverage"]["unresolved_pages"] = [1]
        data["coverage"]["unresolved_reasons"] = {"1": "not_bound"}
    else:
        data["pages"][0]["rotation"] = 90
    with pytest.raises(ValueError):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")


def test_unknown_geometry_preserves_raw_bbox_but_cannot_be_promoted_to_known(tmp_path):
    data = checked(original_pdf(tmp_path))
    cell = data["tables"][0]["cells"]["B2"]
    cell["state"] = "unknown_geometry"
    cell["source"]["bbox"] = [150, 100, 301, 150]
    validate_snapshot(data, data["source"]["sha256"], ".pdf")
    cell["state"] = "known"
    with pytest.raises(ValueError, match="geometry exceeds"):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")


@pytest.mark.parametrize("limits", [ReadLimits(source_bytes=1), ReadLimits(pages=1), ReadLimits(cells=1), ReadLimits(tables=1), ReadLimits(blocks=1), ReadLimits(output_bytes=1)])
def test_native_grid_read_budgets_propagate(tmp_path, limits):
    with pytest.raises(ParserLimitError):
        snapshot(original_pdf(tmp_path, extra_page="grid"), limits=limits)


def test_unresolved_reconstruction_still_consumes_the_cumulative_cell_budget(tmp_path, monkeypatch):
    grid = TableGrid(rows=[10, 20, 30], cols=[10, 20, 30], cells=[CellRegion(0, 0, 0, 1, "first"), CellRegion(0, 0, 1, 1, "overlap")])
    result = SimpleNamespace(groups=[SimpleNamespace(page_results=[SimpleNamespace(page_index=index, success=True, table_grid=grid) for index in (0, 1)])])
    monkeypatch.setattr("documents.pdf.extract_pdf_tables", lambda *args, **kwargs: result)
    with pytest.raises(ParserLimitError, match="cell visits"):
        snapshot(original_pdf(tmp_path, extra_page="grid"), limits=ReadLimits(cells=7))


def test_native_merge_budget_is_not_hidden_as_an_unresolved_page(tmp_path, monkeypatch):
    native_result(monkeypatch, TableGrid(rows=[10, 20, 30], cols=[10, 20, 30], cells=[CellRegion(0, 0, 0, 1, "first"), CellRegion(1, 1, 0, 1, "second")]))
    with pytest.raises(ParserLimitError, match="merges"):
        snapshot(original_pdf(tmp_path), limits=ReadLimits(merges=1))


def test_backend_unavailable_grid_is_distinct_from_other_parser_and_integrity_failures(tmp_path, monkeypatch):
    path = original_pdf(tmp_path)

    def fail_grid(*args, **kwargs):
        raise PdfConversionError("no_successful_groups", "No usable native grid")

    monkeypatch.setattr("documents.pdf.extract_pdf_tables", fail_grid)
    data = checked(path)
    assert data["coverage"]["unresolved_reasons"] == {"1": "native_grid_unavailable"}

    def fail_parser(*args, **kwargs):
        raise PdfConversionError("extract_failed", "Unexpected extraction failure")

    monkeypatch.setattr("documents.pdf.extract_pdf_tables", fail_parser)
    with pytest.raises(PdfConversionError, match="Unexpected extraction"):
        snapshot(path)
    monkeypatch.setattr(document_tools, "digest", lambda path: "0" * 64)
    with pytest.raises(ValueError, match="source changed"):
        snapshot(path)


def test_text_block_only_observation_keeps_existing_contract():
    data = {"source": {"sha256": "a" * 64, "format": ".pdf"}, "tables": [], "pages": [{"page": 1, "blocks": [{"text": "Observed paragraph"}]}]}
    validate_snapshot(deepcopy(data), "a" * 64, ".pdf")


@pytest.mark.parametrize("kind", ["annotation", "widget"])
def test_native_text_under_annotation_or_widget_is_retained_but_unknown(tmp_path, kind):
    original = vector_pdf(tmp_path, native_text=True)
    path = tmp_path / "overlaid.pdf"
    with fitz.open(original) as document:
        page = document[0]
        if kind == "annotation":
            annotation = page.add_highlight_annot(fitz.Rect(30, 65, 100, 90))
            annotation.update()
        else:
            widget = fitz.Widget()
            widget.field_name = "overlay"
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.field_value = "Different"
            widget.rect = fitz.Rect(30, 65, 100, 90)
            page.add_widget(widget)
        document.save(path)
    data = checked(path)
    cells = data["tables"][0]["cells"]
    cell = cells["A2"]
    assert "Native" in cell["value"]
    assert cell["state"] == "unknown_visual_evidence"
    assert cell[f"{kind}_overlap"] is True
    # Some annotation appearances also exist in the original get_drawings()
    # list. Such paths retain real source indices rather than invented ones.
    with fitz.open(path) as document:
        assert data["tables"][0]["vector_path_count"] == len(document[0].get_drawings())
    assert not cell.get("image_overlap")
    assert cells["B2"]["state"] == "known"
    cell["state"] = "known"
    with pytest.raises(ValueError, match="uninterpreted visual"):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")


@pytest.mark.parametrize("kind", ["annotation", "widget"])
def test_graphic_outside_native_cell_does_not_change_its_state(tmp_path, monkeypatch, kind):
    cells = [CellRegion(row, row, col, col, "Native" if (row, col) == (1, 0) else "") for row in range(2) for col in range(2)]
    native_result(monkeypatch, TableGrid(rows=[20, 60, 100], cols=[20, 140, 260], cells=cells))
    original = vector_pdf(tmp_path, native_text=True)
    path = tmp_path / "outside.pdf"
    with fitz.open(original) as document:
        page = document[0]
        if kind == "annotation":
            page.add_rect_annot(fitz.Rect(5, 102, 30, 115)).update()
        else:
            widget = fitz.Widget()
            widget.field_name = "outside"
            widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
            widget.field_value = "Other"
            widget.rect = fitz.Rect(5, 102, 30, 115)
            page.add_widget(widget)
        document.save(path)
    cell = checked(path)["tables"][0]["cells"]["A2"]
    assert (cell["value"], cell["state"]) == ("Native", "known")
    assert not cell.get(f"{kind}_overlap")


@pytest.mark.parametrize("flag", ["annotation_overlap", "widget_overlap"])
def test_pdf_visual_overlap_flags_must_be_boolean(tmp_path, flag):
    data = checked(original_pdf(tmp_path))
    data["tables"][0]["cells"]["B2"][flag] = "true"
    with pytest.raises(ValueError, match="overlap observation"):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")


@pytest.mark.parametrize("kind", ["annotation", "widget"])
def test_annotation_and_widget_observations_do_not_invent_vector_paths(kind):
    from audit.pdf_tables import _table

    page = SimpleNamespace(
        number=0,
        rect=fitz.Rect(0, 0, 100, 100),
        get_image_info=lambda: [],
        get_drawings=lambda: [],
        annots=lambda: [SimpleNamespace(rect=fitz.Rect(20, 20, 30, 30))] if kind == "annotation" else [],
        widgets=lambda: [SimpleNamespace(rect=fitz.Rect(20, 20, 30, 30))] if kind == "widget" else [],
    )
    table = _table(TableGrid(rows=[10, 50], cols=[10, 50], cells=[CellRegion(0, 0, 0, 0, "Native")]), page, ReadLimits(), 1.0)
    cell = table["cells"]["A1"]
    assert (cell["value"], cell["state"], cell[f"{kind}_overlap"]) == ("Native", "unknown_visual_evidence", True)
    assert table["vector_path_count"] == 0
    assert not cell.get("vector_path_indices")
    assert not cell.get("visual_overlap")


def test_later_full_cell_mask_cannot_be_promoted_to_a_known_native_value(tmp_path):
    source = vector_pdf(tmp_path, native_text=True)
    masked = tmp_path / "masked.pdf"
    with fitz.open(source) as document:
        document[0].draw_rect(fitz.Rect(20, 60, 140, 100), color=None, fill=(1, 1, 1))
        document.save(masked)
    data = checked(masked)
    cell = data["tables"][0]["cells"]["A2"]
    assert cell["value"] == "Native"
    assert cell["state"] == "unknown_visual_evidence"
    assert cell["native_text_unobscured"] is False
    cell["state"] = "known"
    with pytest.raises(ValueError, match="uninterpreted visual"):
        validate_snapshot(data, data["source"]["sha256"], ".pdf")
