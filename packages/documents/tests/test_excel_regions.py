"""Text-located Excel fields preserve the original archive and marker geometry."""

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
import pytest
from documents.excel import visual
from documents.excel.loader import ExcelLoader, ExcelLoadOptions
from openpyxl import Workbook
from PIL import Image


def converted_book() -> bytes:
    book = Workbook()
    book.active.title = "cover"
    book.active["A1"] = "cover page"
    target = book.create_sheet("requested ' header")
    target["A1"] = "original header"
    target["D13"] = "body outside requested rows"
    book.create_sheet("other")
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    with zipfile.ZipFile(stream, "a") as archive:
        archive.writestr("xl/drawings/drawing1.xml", b"original drawing bytes and order")
    return stream.getvalue()


def test_print_copy_selects_named_sheet_and_field_without_rebuilding_drawings():
    original = converted_book()
    result = visual._region_print_copy(original, sheet_name="requested ' header", cell_range="A4:D4", max_bytes=1000000)
    with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(io.BytesIO(result)) as after:
        workbook = ET.fromstring(after.read("xl/workbook.xml"))
        sheets = workbook.find(f"{{{visual._MAIN}}}sheets")
        assert [(sheet.get("name"), sheet.get("state")) for sheet in sheets] == [("cover", "hidden"), ("requested ' header", "visible"), ("other", "hidden")]
        areas = workbook.findall(f"{{{visual._MAIN}}}definedNames/{{{visual._MAIN}}}definedName")
        assert [(area.get("localSheetId"), area.text) for area in areas] == [("1", "'requested '' header'!$A$4:$D$4")]
        for name in before.namelist():
            if name not in {"xl/workbook.xml", "xl/worksheets/sheet2.xml"}:
                assert after.read(name) == before.read(name)
        target = ET.fromstring(after.read("xl/worksheets/sheet2.xml"))
        assert target.find(f"{{{visual._MAIN}}}pageSetup").get("fitToWidth") == "1"
        assert target.find(f"{{{visual._MAIN}}}pageSetup").get("fitToHeight") == "1"


def test_print_copy_cannot_choose_first_sheet_when_requested_sheet_is_missing():
    with pytest.raises(visual.ExcelRegionRenderError) as error:
        visual._region_print_copy(converted_book(), sheet_name="missing", cell_range="A4:D4", max_bytes=1000000)
    assert error.value.code == "sheet_binding_failed"


def test_xls_cells_come_from_the_original_without_libreoffice(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("cell reads must not convert the workbook")

    monkeypatch.setattr("documents.excel.legacy.convert_excel", refuse)
    source = Path(__file__).resolve().parents[3] / "assets" / "nio" / "samples" / "Cetus Spoiler-有特殊特性清单" / "控制计划 -CETUS.xls"
    workbook = ExcelLoader.load_workbook(str(source), options=ExcelLoadOptions(data_only=False))
    try:
        assert workbook.sheetnames == ["扰流板"]
        assert workbook["扰流板"]["A9"].value == "CETUS扰流板"
    finally:
        workbook.close()


def test_only_rendering_converts_an_xls_source(monkeypatch):
    calls = []

    def convert(content, **kwargs):
        calls.append(content)
        return b"PK-converted"

    monkeypatch.setattr(visual, "convert_excel", convert)
    visual.renderable_xlsx.cache_clear()
    original = ExcelLoader.OLE_MAGIC + b"controlled binary source"
    assert visual.renderable_xlsx(original) == b"PK-converted"
    assert visual.renderable_xlsx(b"PK-xlsx") == b"PK-xlsx"
    assert calls == [original]
    visual.renderable_xlsx.cache_clear()


def test_render_crops_text_and_its_marker_instead_of_a_fixed_header(monkeypatch):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((100, 400), "Production", fontsize=12)
        page.draw_rect((175, 388, 188, 401), fill=(0, 0, 0))
        pdf = document.tobytes()
    monkeypatch.setattr(visual, "convert_excel", lambda *a, **kw: pdf)
    png = visual.render_xlsx_field(converted_book(), sheet_name="requested ' header", cell_range="A4:D4", keywords=("Production",))
    with Image.open(io.BytesIO(png)) as image:
        assert image.width < 1200 and image.height < 400
        # The black marker is retained to the right of the locating text.
        gray = image.convert("L")
        assert min(gray.crop((gray.width // 2, 0, gray.width, gray.height)).getdata()) == 0
