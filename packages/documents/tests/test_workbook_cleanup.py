"""Workbook loading respects ownership of temporary conversion files."""

import pytest
from documents.contracts import (
    ConversionArtifact,
    FileRef,
)
from documents.conversion import load_workbook_auto
from openpyxl import Workbook


@pytest.mark.parametrize("cleanup", [True, False], ids=["pipeline-owned", "caller-owned"])
def test_invalid_converted_workbook_preserves_the_load_error_and_respects_cleanup(tmp_path, monkeypatch, cleanup):
    converted = tmp_path / "converted.xlsx"
    converted.write_bytes(b"not an Excel workbook")
    auxiliary = tmp_path / "conversion.html"
    auxiliary.write_text("conversion diagnostic", encoding="utf-8")
    source = "https://files.example.test/flow.PDF?signature=fixture#page=1"

    def convert(file_ref, *, enable_cache=True):
        assert file_ref == FileRef(location=source)
        assert enable_cache is True
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("documents.conversion.pipeline.convert_pdf_to_excel_auto", convert)

    with pytest.raises(Exception, match="无法加载文件"):
        load_workbook_auto(source, cleanup=cleanup)

    assert converted.exists() is not cleanup
    assert auxiliary.exists() is not cleanup


@pytest.mark.parametrize("cleanup", [True, False], ids=["pipeline-owned", "caller-owned"])
def test_valid_converted_workbook_remains_readable_and_respects_cleanup(tmp_path, monkeypatch, cleanup):
    converted = tmp_path / "converted.xlsx"
    workbook = Workbook()
    workbook.active.append(["工序号", "工序名称"])
    workbook.active.append([10, "切割"])
    workbook.save(converted)
    workbook.close()
    auxiliary = tmp_path / "conversion.html"
    auxiliary.write_text("conversion diagnostic", encoding="utf-8")

    def convert(file_ref, *, enable_cache=True):
        return ConversionArtifact(success=True, xlsx_path=str(converted), temp_files=[str(converted), str(auxiliary)])

    monkeypatch.setattr("documents.conversion.pipeline.convert_pdf_to_excel_auto", convert)

    loaded = load_workbook_auto("flow.pdf", cleanup=cleanup)
    try:
        assert list(loaded.active.values) == [("工序号", "工序名称"), (10, "切割")]
        assert converted.exists() is not cleanup
        assert auxiliary.exists() is not cleanup
    finally:
        loaded.close()


@pytest.mark.parametrize("cleanup", [True, False], ids=["default-cleanup", "no-cleanup"])
def test_original_excel_is_not_a_temporary_conversion_file(tmp_path, cleanup):
    source = tmp_path / "original.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "原始资料"
    workbook.save(source)
    workbook.close()
    original_bytes = source.read_bytes()

    loaded = load_workbook_auto(str(source), cleanup=cleanup)
    try:
        assert loaded.active["A1"].value == "原始资料"
    finally:
        loaded.close()

    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("graphic", ["none", "vector", "image", "annotation", "widget"])
def test_native_conversion_keeps_known_cells_and_marks_local_graphics_unknown(tmp_path, graphic):
    import fitz
    from documents.pdf._writer import write_workbook
    from documents.pdf.result import GroupExtraction
    from documents.pdf.source_graphics import annotate_native_cells
    from documents.pdf.spec import OutputSpec, StitchMode
    from documents.table.models import CellRegion, TableGrid
    from openpyxl import load_workbook

    source = tmp_path / "source.pdf"
    with fitz.open() as document:
        page = document.new_page(width=220, height=110)
        for y in (10, 50, 90):
            page.draw_line((10, y), (210, y))
        for x in (10, 110, 210):
            page.draw_line((x, 10), (x, 90))
        page.insert_text((20, 35), "header")
        page.insert_text((120, 35), "value")
        page.insert_text((20, 75), "key")
        if graphic == "vector":
            page.draw_line((145, 65), (145, 85))
        elif graphic == "image":
            import io

            from PIL import Image

            data = io.BytesIO()
            Image.new("RGB", (20, 20), color="black").save(data, format="PNG")
            page.insert_image(fitz.Rect(140, 60, 160, 80), stream=data.getvalue())
        elif graphic == "annotation":
            page.add_rect_annot(fitz.Rect(140, 60, 160, 80))
        elif graphic == "widget":
            widget = fitz.Widget()
            widget.field_name = "selection"
            widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            widget.rect = fitz.Rect(140, 60, 160, 80)
            page.add_widget(widget)
        document.save(source)
    grid = TableGrid([10.0, 50.0, 90.0], [10.0, 110.0, 210.0], [CellRegion(0, 0, 0, 0, "header"), CellRegion(0, 0, 1, 1, "value"), CellRegion(1, 1, 0, 0, "key"), CellRegion(1, 1, 1, 1, "")])
    annotate_native_cells(source.read_bytes(), 0, grid, tolerance=1.0)
    group = GroupExtraction(0, (0,), StitchMode.NO_STITCH, True, [], grid, 4, 9)
    metadata = []
    target = tmp_path / "native.xlsx"
    assert write_workbook([group], str(target), OutputSpec(), unknown_cells=metadata)
    with target.open("rb") as handle:
        workbook = load_workbook(handle)
        try:
            assert workbook.active["A1"].value == "header"
            assert workbook.active["B1"].value == "value"
            assert workbook.active["A2"].value == "key"
            if graphic == "none":
                assert workbook.active["B2"].value is None
                assert metadata == []
            else:
                assert workbook.active["B2"].value == "#N/A"
                assert workbook.active["B2"].data_type == "e"
                assert metadata == [
                    {"sheet": workbook.active.title, "row": 2, "column": 2, "end_row": 2, "end_column": 2, "source_page": 1, "source_bbox": (110.0, 50.0, 210.0, 90.0), "native_value": ""}
                ]
        finally:
            workbook.close()


def test_scanned_pdf_does_not_automatically_dispatch_ocr(tmp_path, monkeypatch):
    import fitz
    from documents.conversion import pipeline
    from documents.ocr import client
    from documents.ocr.paddle import reader

    source = tmp_path / "scan.pdf"
    with fitz.open() as document:
        document.new_page()
        document.save(source)

    def forbidden(*args, **kwargs):
        pytest.fail("table conversion must not automatically request external OCR")

    monkeypatch.setattr(client, "_get_llm_client", forbidden)
    monkeypatch.setattr(reader.PaddleStructureService, "analyze_page", forbidden)
    artifact = pipeline.convert_pdf_to_excel_auto(str(source))
    assert artifact.success is False
    assert artifact.diagnostics["error_code"] == "native_table_unconfirmed"
    assert artifact.diagnostics["fallback_ocr_triggered"] is False


def test_native_merged_cell_keeps_text_when_adjacent_background_spills_at_grid_edge():
    import fitz
    from documents.pdf.source_graphics import annotate_native_cells
    from documents.table.models import CellRegion, TableGrid

    with fitz.open() as document:
        page = document.new_page(width=300, height=100)
        # The left neighbour is wider than the extractor's outer margin. Its
        # background intrudes 0.4pt into the merged cell; all row edges agree.
        page.draw_rect(fitz.Rect(61.08, 20, 71.16, 40.12), color=None, fill=(1, 1, 1))
        page.draw_rect(fitz.Rect(71.04, 10, 97.8, 20.12), color=None, fill=(1, 1, 1))
        page.draw_rect(fitz.Rect(69.72, 10, 71.16, 60), color=None, fill=(0, 0, 0))
        page.insert_text((80, 35), "native supplier")
        source = document.tobytes()
    grid = TableGrid([10.0, 20.0, 40.0], [59.8, 70.75, 97.7, 151.0, 251.7], [CellRegion(1, 1, 1, 3, "native supplier")])
    annotate_native_cells(source, 0, grid, tolerance=1.15)
    assert grid.cells[0].is_unknown is False
    assert grid.cells[0].content == "native supplier"


@pytest.mark.parametrize("bounds", [(70.5, 24, 73, 28), (90, 24, 110, 30), (70.5, 20, 75, 40)])
def test_native_cell_graphic_is_not_hidden_by_background_sliver_exception(bounds):
    import fitz
    from documents.pdf.source_graphics import annotate_native_cells
    from documents.table.models import CellRegion, TableGrid

    with fitz.open() as document:
        page = document.new_page(width=300, height=100)
        page.draw_rect(fitz.Rect(*bounds), color=None, fill=(0, 0, 0))
        source = document.tobytes()
    grid = TableGrid([20.0, 40.0], [70.75, 151.0], [CellRegion(0, 0, 0, 0, "known text")])
    annotate_native_cells(source, 0, grid, tolerance=1.15)
    assert grid.cells[0].is_unknown is True
    assert grid.cells[0].source_value == "known text"


@pytest.mark.parametrize("vertical", [False, True])
def test_native_cell_keeps_label_when_adjacent_thin_borders_only_graze_corners(vertical):
    import fitz
    from documents.pdf.source_graphics import native_visual_overlaps

    cell = [100, 100, 160, 110]
    borders = [[20, 109.2, 100.05, 110.05], [159.2, 109.2, 220, 110.05]]
    if vertical:
        cell = [cell[1], cell[0], cell[3], cell[2]]
        borders = [[bounds[1], bounds[0], bounds[3], bounds[2]] for bounds in borders]
    with fitz.open() as document:
        page = document.new_page()
        for bounds in borders:
            page.draw_rect(fitz.Rect(*bounds), color=None, fill=(0, 0, 0))
        assert native_visual_overlaps(page, [cell], tolerance=1.0) == [False]


@pytest.mark.parametrize("bounds", [(159.2, 106, 220, 110.05), (140, 109.2, 150, 110.05), (159.2, 103, 170, 107)])
def test_native_cell_corner_exception_keeps_interior_marks_unknown(bounds):
    import fitz
    from documents.pdf.source_graphics import native_visual_overlaps

    with fitz.open() as document:
        page = document.new_page()
        page.draw_rect(fitz.Rect(*bounds), color=None, fill=(0, 0, 0))
        assert native_visual_overlaps(page, [[100, 100, 160, 110]], tolerance=1.0) == [True]


@pytest.mark.parametrize("bounds", [(42.18, 105.36, 52.86, 115.2), (42.18, 93, 52.86, 105.5)])
def test_native_label_keeps_text_when_neighbour_background_extends_beyond_grid(bounds):
    import fitz
    from documents.pdf.source_graphics import native_visual_overlaps

    with fitz.open() as document:
        page = document.new_page()
        page.draw_rect(fitz.Rect(*bounds), color=None, fill=(1, 1, 1))
        assert native_visual_overlaps(page, [[52.65, 105.4, 81.5, 114.05]], tolerance=1.0) == [False]


@pytest.mark.parametrize("mark,expected", [("underline", False), ("crossout", True), ("short_mark", True), ("extra_stroke", True)])
def test_native_text_underline_does_not_hide_other_graphic_marks(mark, expected):
    import fitz
    from documents.pdf.source_graphics import native_visual_overlaps

    with fitz.open() as document:
        page = document.new_page()
        text = "6B201CH_LAA-11040014"
        page.insert_text((100, 112), text, fontsize=7)
        end = 100 + fitz.get_text_length(text, fontsize=7)
        y = 109 if mark == "crossout" else 112.7
        page.draw_line((100, y), (end - 10 if mark == "short_mark" else end, y), width=0.5)
        if mark == "extra_stroke":
            page.draw_line((120, 105), (125, 111), width=0.5)
        assert native_visual_overlaps(page, [[90, 104, 210, 114]]) == [expected]


@pytest.mark.parametrize("mark", ["vector", "image", "annotation", "widget"])
def test_native_label_is_not_erased_by_a_separate_graphic_in_its_cell(mark):
    import io

    import fitz
    from documents.pdf.source_graphics import annotate_native_cells
    from documents.table.models import CellRegion, TableGrid
    from PIL import Image

    with fitz.open() as document:
        page = document.new_page(width=220, height=100)
        page.insert_text((20, 35), "Header")
        if mark == "vector":
            page.draw_line((140, 20), (160, 40))
        elif mark == "image":
            data = io.BytesIO()
            Image.new("RGB", (10, 10), "black").save(data, format="PNG")
            page.insert_image(fitz.Rect(140, 20, 160, 40), stream=data.getvalue())
        elif mark == "annotation":
            page.add_rect_annot(fitz.Rect(140, 20, 160, 40))
        else:
            widget = fitz.Widget()
            widget.field_name = "separate-choice"
            widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            widget.rect = fitz.Rect(140, 20, 160, 40)
            page.add_widget(widget)
        source = document.tobytes()
    grid = TableGrid([10.0, 60.0], [10.0, 200.0], [CellRegion(0, 0, 0, 0, "Header")])
    annotate_native_cells(source, 0, grid, tolerance=1.0)
    assert grid.cells[0].is_unknown is False
    assert grid.cells[0].content == "Header"


@pytest.mark.parametrize("after", [False, True])
def test_native_text_visibility_respects_full_cell_fill_paint_order(after):
    import fitz
    from documents.pdf.source_graphics import annotate_native_cells
    from documents.table.models import CellRegion, TableGrid

    with fitz.open() as document:
        page = document.new_page(width=220, height=100)
        if not after:
            page.draw_rect(fitz.Rect(10, 10, 200, 60), color=None, fill=(1, 1, 1))
        page.insert_text((20, 35), "Header")
        if after:
            page.draw_rect(fitz.Rect(10, 10, 200, 60), color=None, fill=(1, 1, 1))
        source = document.tobytes()
    grid = TableGrid([10.0, 60.0], [10.0, 200.0], [CellRegion(0, 0, 0, 0, "Header")])
    annotate_native_cells(source, 0, grid, tolerance=1.0)
    assert grid.cells[0].is_unknown is after
