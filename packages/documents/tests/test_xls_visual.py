"""Requested XLS headers retain drawing bytes and exact sheet/range bindings."""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
import pytest
from documents.excel import visual
from documents.word.legacy import LegacyDocConversionError, LegacyDocConversionLimits
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


def test_print_copy_selects_named_sheet_and_header_without_rebuilding_drawings():
    original = converted_book()
    result = visual._header_print_copy(original, sheet_name="requested ' header", header_rows=12, columns=4, max_bytes=1000000)
    with zipfile.ZipFile(io.BytesIO(original)) as before, zipfile.ZipFile(io.BytesIO(result)) as after:
        workbook = ET.fromstring(after.read("xl/workbook.xml"))
        sheets = workbook.find(f"{{{visual._MAIN}}}sheets")
        assert [(sheet.get("name"), sheet.get("state")) for sheet in sheets] == [("cover", "hidden"), ("requested ' header", "visible"), ("other", "hidden")]
        areas = workbook.findall(f"{{{visual._MAIN}}}definedNames/{{{visual._MAIN}}}definedName")
        assert [(area.get("localSheetId"), area.text) for area in areas] == [("1", "'requested '' header'!$A$1:$D$12")]
        for name in before.namelist():
            if name not in {"xl/workbook.xml", "xl/worksheets/sheet2.xml"}:
                assert after.read(name) == before.read(name)
        target = ET.fromstring(after.read("xl/worksheets/sheet2.xml"))
        assert target.find(f"{{{visual._MAIN}}}pageSetup").get("fitToWidth") == "1"
        assert target.find(f"{{{visual._MAIN}}}pageSetup").get("fitToHeight") == "1"


def test_print_copy_cannot_choose_first_sheet_when_requested_sheet_is_missing():
    with pytest.raises(visual.XlsHeaderRenderError) as error:
        visual._header_print_copy(converted_book(), sheet_name="missing", header_rows=12, columns=4, max_bytes=1000000)
    assert error.value.code == "sheet_binding_failed"


@pytest.mark.parametrize("rows", [0, 101, True, "12"])
def test_invalid_header_range_does_not_start_converter(monkeypatch, rows):
    monkeypatch.setattr(visual, "_run_converter", lambda *a, **k: pytest.fail("must not start converter"))
    with pytest.raises(visual.XlsHeaderRenderError) as error:
        visual.render_xls_header(b"input", sheet_name="sheet", header_rows=rows)
    assert error.value.code == "invalid_header_rows"


def test_invalid_xls_content_does_not_start_converter(monkeypatch):
    monkeypatch.setattr(visual, "_run_converter", lambda *a, **k: pytest.fail("must not start converter"))
    with pytest.raises(visual.XlsHeaderRenderError) as error:
        visual.render_xls_header(b"not an XLS", sheet_name="sheet")
    assert error.value.code == "invalid_source_sheet"


@pytest.mark.parametrize(
    "records,entries,expected_code",
    [
        (struct.pack("<HH", 0x01AE, 6) + b"\x01\x00\x02\x00xx", [["Workbook"]], "external_source_reference"),
        (struct.pack("<HH", 0x0085, 6) + b"\x00" * 5 + b"\x01", [["Workbook"]], "macro_source_unsupported"),
        (b"", [["Workbook"], ["_VBA_PROJECT_CUR", "VBA", "module"]], "macro_source_unsupported"),
    ],
    ids=["external-workbook", "xlm-macro-sheet", "vba-project"],
)
def test_external_source_or_macro_is_rejected_before_office_import(monkeypatch, records, entries, expected_code):
    class Compound:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def listdir(self):
            return entries

        def exists(self, name):
            return name == "Workbook"

        def openstream(self, name):
            return io.BytesIO(records)

    monkeypatch.setattr(visual.olefile, "OleFileIO", lambda *a: Compound())
    monkeypatch.setattr(visual.xlrd, "open_workbook", lambda **k: pytest.fail("must reject before import"))
    with pytest.raises(visual.XlsHeaderRenderError) as error:
        visual._source_sheet(b"source", "sheet", LegacyDocConversionLimits())
    assert error.value.code == expected_code


def test_render_uses_clean_bounded_conversion_and_returns_one_header_page(monkeypatch):
    calls = []
    monkeypatch.setenv("OCR_OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("HTTP_PROXY", "must-not-inherit")
    monkeypatch.setattr(visual, "_source_sheet", lambda *a: 4)
    monkeypatch.setattr(visual, "_resolve_executable", lambda *a: "/controlled/soffice")

    def convert(command, *, environment, limits):
        calls.append(command)
        assert "OCR_OPENAI_API_KEY" not in environment
        assert "HTTP_PROXY" not in environment
        assert environment["HOME"] == environment["TMPDIR"]
        assert limits.timeout_seconds == 45
        assert limits.max_input_bytes == 25 * 1024 * 1024
        profile = Path(next(argument.split("=", 1)[1] for argument in command if argument.startswith("-env:UserInstallation="))[7:])
        config = (profile / "user/registrymodifications.xcu").read_text()
        assert 'oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value>' in config
        assert 'oor:name="Link" oor:op="fuse"><value>1</value>' in config
        assert Path(environment["FONTCONFIG_FILE"]).is_file()
        output = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        if source.suffix == ".xls":
            assert source.read_bytes() == b"original binary XLS"
            (output / "source.xlsx").write_bytes(converted_book())
        else:
            with zipfile.ZipFile(source) as archive:
                assert b"$D$12" in archive.read("xl/workbook.xml")
                assert archive.read("xl/drawings/drawing1.xml") == b"original drawing bytes and order"
            with fitz.open() as document:
                page = document.new_page(width=300, height=100)
                page.insert_text((10, 30), "bound original header")
                document.save(output / "header.pdf")
        return 0, b"", b""

    monkeypatch.setattr(visual, "_run_converter", convert)
    result = visual.render_xls_header(b"original binary XLS", sheet_name="requested ' header")
    with Image.open(io.BytesIO(result)) as image:
        assert image.format == "PNG"
        assert image.size == (600, 200)
    assert len(calls) == 2
    assert not Path(calls[0][-1]).parent.exists()


def test_render_does_not_publish_first_page_of_ambiguous_output(monkeypatch):
    monkeypatch.setattr(visual, "_source_sheet", lambda *a: 4)
    monkeypatch.setattr(visual, "_resolve_executable", lambda *a: "/controlled/soffice")

    def convert(command, **kwargs):
        output = Path(command[command.index("--outdir") + 1])
        if Path(command[-1]).suffix == ".xls":
            (output / "source.xlsx").write_bytes(converted_book())
        else:
            with fitz.open() as document:
                document.new_page()
                document.new_page()
                document.save(output / "header.pdf")
        return 0, b"", b""

    monkeypatch.setattr(visual, "_run_converter", convert)
    with pytest.raises(visual.XlsHeaderRenderError) as error:
        visual.render_xls_header(b"original", sheet_name="requested ' header")
    assert error.value.code == "header_coverage_unconfirmed"


def test_missing_local_sandbox_is_reported_without_retry(monkeypatch):
    monkeypatch.setattr(visual, "_source_sheet", lambda *a: 4)
    monkeypatch.setattr(visual, "_resolve_executable", lambda *a: "/controlled/soffice")
    calls = []

    def reject(command, **kwargs):
        calls.append(command)
        raise LegacyDocConversionError("sandbox_unavailable", "blocked")

    monkeypatch.setattr(visual, "_run_converter", reject)
    with pytest.raises(visual.XlsHeaderRenderError) as error:
        visual.render_xls_header(b"source", sheet_name="sheet")
    assert error.value.code == "sandbox_unavailable"
    assert len(calls) == 1
