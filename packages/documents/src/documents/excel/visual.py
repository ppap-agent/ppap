"""Bounded rendering of a requested legacy XLS header, including source drawings."""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

import fitz
import olefile
import xlrd
from openpyxl.utils.cell import get_column_letter

from documents.word.legacy import LegacyDocConversionError, LegacyDocConversionLimits, _local_environment, _resolve_executable, _run_converter

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class XlsHeaderRenderError(ValueError):
    """The requested source header cannot be rendered with a proven binding."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _source_sheet(content: bytes, sheet_name: str, limits: LegacyDocConversionLimits) -> int:
    if len(content) > limits.max_input_bytes:
        raise XlsHeaderRenderError("input_too_large", "旧 XLS 超过本地转换大小限制")
    try:
        with olefile.OleFileIO(io.BytesIO(content)) as compound:
            if any(part.casefold() in {"vba", "_vba_project_cur"} for path in compound.listdir() for part in path):
                raise XlsHeaderRenderError("macro_source_unsupported", "含宏的旧 XLS 不进入原件渲染")
            stream = next((name for name in ("Workbook", "Book") if compound.exists(name)), None)
            if stream is None:
                raise ValueError("missing workbook stream")
            records = compound.openstream(stream).read()
        # External workbook / DDE links can read another source during import.
        # Internal SUPBOOK is the BIFF marker 0x0401; all others stay unavailable.
        offset = 0
        while offset + 4 <= len(records):
            opcode, size = struct.unpack_from("<HH", records, offset)
            data = records[offset + 4 : offset + 4 + size]
            offset += 4 + size
            if opcode == 0x0085 and len(data) >= 6 and data[5] in {1, 6}:
                raise XlsHeaderRenderError("macro_source_unsupported", "含宏工作表的旧 XLS 不进入原件渲染")
            if opcode == 0x01AE and (len(data) < 4 or data[2:4] != b"\x01\x04"):
                raise XlsHeaderRenderError("external_source_reference", "旧 XLS 引用了其他来源，已拒绝自动读取")
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
        try:
            sheet = workbook.sheet_by_name(sheet_name)
            if sheet.visibility:
                raise XlsHeaderRenderError("hidden_source_sheet", "请求的原始工作表不可见")
            if not sheet.ncols:
                raise XlsHeaderRenderError("empty_source_sheet", "请求的原始工作表没有列")
            return sheet.ncols
        finally:
            workbook.release_resources()
    except XlsHeaderRenderError:
        raise
    except (OSError, ValueError, xlrd.XLRDError) as exc:
        raise XlsHeaderRenderError("invalid_source_sheet", "无法确认原始 XLS 或请求的工作表") from exc


def _header_print_copy(content: bytes, *, sheet_name: str, header_rows: int, columns: int, max_bytes: int) -> bytes:
    """Change print metadata only; preserve the converted source drawing parts."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > max_bytes:
            raise XlsHeaderRenderError("output_too_large", "转换后的工作簿超过大小限制")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_list = workbook.find(f"{{{_MAIN}}}sheets")
        sheets = list(sheet_list) if sheet_list is not None else []
        selected = [sheet for sheet in sheets if sheet.get("name") == sheet_name]
        if len(selected) != 1:
            raise XlsHeaderRenderError("sheet_binding_failed", "转换后的工作表与请求不一致")
        target = selected[0]
        index = sheets.index(target)
        for sheet in sheets:
            sheet.set("state", "visible" if sheet is target else "hidden")
        for view in workbook.findall(f"{{{_MAIN}}}bookViews/{{{_MAIN}}}workbookView"):
            view.set("activeTab", str(index))
        names = workbook.find(f"{{{_MAIN}}}definedNames")
        if names is None:
            names = ET.Element(f"{{{_MAIN}}}definedNames")
            calculation = workbook.find(f"{{{_MAIN}}}calcPr")
            workbook.insert(list(workbook).index(calculation) if calculation is not None else len(workbook), names)
        for name in list(names):
            if name.get("name") in {"_xlnm.Print_Area", "_xlnm.Print_Titles"}:
                names.remove(name)
        quoted = sheet_name.replace("'", "''")
        area = ET.SubElement(names, f"{{{_MAIN}}}definedName", name="_xlnm.Print_Area", localSheetId=str(index))
        area.text = f"'{quoted}'!$A$1:${get_column_letter(columns)}${header_rows}"
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationship_id = target.get(f"{{{_REL}}}id")
        relations = [item for item in relationships if item.get("Id") == relationship_id and item.get("TargetMode") != "External"]
        if len(relations) != 1:
            raise XlsHeaderRenderError("sheet_binding_failed", "无法确认转换后的工作表原件关联")
        relative = relations[0].get("Target", "")
        part = relative.lstrip("/") if relative.startswith("/") else "xl/" + relative
        if ".." in Path(part).parts:
            raise XlsHeaderRenderError("sheet_binding_failed", "工作表关联越界")
        worksheet = ET.fromstring(archive.read(part))
        properties = worksheet.find(f"{{{_MAIN}}}sheetPr")
        if properties is None:
            properties = ET.Element(f"{{{_MAIN}}}sheetPr")
            worksheet.insert(0, properties)
        page_properties = properties.find(f"{{{_MAIN}}}pageSetUpPr")
        if page_properties is None:
            page_properties = ET.SubElement(properties, f"{{{_MAIN}}}pageSetUpPr")
        page_properties.set("fitToPage", "1")
        setup = worksheet.find(f"{{{_MAIN}}}pageSetup")
        if setup is None:
            setup = ET.SubElement(worksheet, f"{{{_MAIN}}}pageSetup")
        setup.attrib.update({"orientation": "landscape", "paperSize": "8", "fitToWidth": "1", "fitToHeight": "1"})
        for setting in ("scale", "firstPageNumber", "useFirstPageNumber", "paperWidth", "paperHeight"):
            setup.attrib.pop(setting, None)
        replacements = {"xl/workbook.xml": ET.tostring(workbook, encoding="utf-8", xml_declaration=True), part: ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as destination:
            for info in archive.infolist():
                destination.writestr(info, replacements.get(info.filename, archive.read(info.filename)))
        return output.getvalue()


def render_xls_header(content: bytes, *, sheet_name: str, header_rows: int = 12) -> bytes:
    """Return a PNG of exactly one source sheet's requested header row range."""
    if type(header_rows) is not int or not 1 <= header_rows <= 100:
        raise XlsHeaderRenderError("invalid_header_rows", "页眉范围必须为 1 至 100 行")
    limits = LegacyDocConversionLimits()
    columns = _source_sheet(content, sheet_name, limits)
    try:
        converter = _resolve_executable(None)
        with TemporaryDirectory(prefix="ppap-xls-header-") as directory:
            work = Path(directory)
            profile = work / "profile"
            user = profile / "user"
            user.mkdir(parents=True)
            # A fresh profile has no trusted locations or certificates. Level 3
            # prohibits document macros; links must not update during import.
            (user / "registrymodifications.xcu").write_text(
                '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item></oor:items>',
                encoding="utf-8",
            )
            environment = dict(_local_environment(work))
            font_config = work / "fonts.conf"
            font_directories = [Path(path) for path in ("/usr/share/fonts", "/usr/local/share/fonts", "/System/Library/Fonts", "/Library/Fonts") if Path(path).is_dir()]
            fonts = ET.Element("fontconfig")
            for path in font_directories:
                ET.SubElement(fonts, "dir").text = str(path)
            ET.SubElement(fonts, "cachedir").text = str(work / "font-cache")
            font_config.write_bytes(ET.tostring(fonts, encoding="utf-8", xml_declaration=True))
            environment["FONTCONFIG_FILE"] = str(font_config)
            source = work / "source.xls"
            source.write_bytes(content)
            output = work / "output"
            output.mkdir()

            def convert(path: Path, filter_name: str, suffix: str) -> bytes:
                command = (
                    converter,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--nolockcheck",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    filter_name,
                    "--outdir",
                    str(output),
                    str(path),
                )
                code, _stdout, _stderr = _run_converter(command, environment=environment, limits=limits)
                target = output / f"{path.stem}.{suffix}"
                if code or not target.is_file():
                    raise XlsHeaderRenderError("conversion_failed", "本地 Calc 未生成请求的页眉原件")
                if target.stat().st_size > limits.max_output_bytes:
                    raise XlsHeaderRenderError("output_too_large", "渲染后的页眉超过大小限制")
                return target.read_bytes()

            converted = convert(source, "xlsx:Calc MS Excel 2007 XML", "xlsx")
            header = work / "header.xlsx"
            header.write_bytes(_header_print_copy(converted, sheet_name=sheet_name, header_rows=header_rows, columns=columns, max_bytes=limits.max_output_bytes))
            pdf = convert(header, "pdf:calc_pdf_Export", "pdf")
            with fitz.open(stream=pdf, filetype="pdf") as document:
                if len(document) != 1:
                    raise XlsHeaderRenderError("header_coverage_unconfirmed", "页眉导出不是唯一页面，无法确认原件范围")
                page = document[0]
                if page.rect.width * page.rect.height * 16 > limits.max_output_bytes:
                    raise XlsHeaderRenderError("output_too_large", "页眉图像超过本地渲染大小限制")
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                return pixmap.tobytes("png")
    except XlsHeaderRenderError:
        raise
    except LegacyDocConversionError as exc:
        raise XlsHeaderRenderError(exc.code, "旧 XLS 原件渲染所需的受限本地 Calc 不可用") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise XlsHeaderRenderError("render_failed", "无法安全渲染请求的旧 XLS 页眉") from exc
