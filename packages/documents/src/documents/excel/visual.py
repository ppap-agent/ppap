"""Text-located XLSX field rendering, shared by every Excel source format."""

import io
import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from functools import lru_cache

import fitz
from openpyxl.utils.cell import absolute_coordinate, range_boundaries

from documents.excel.legacy import convert_excel
from documents.excel.loader import ExcelLoader
from documents.word.legacy import LegacyDocConversionLimits

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class ExcelRegionRenderError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _region_print_copy(content: bytes, *, sheet_name: str, cell_range: str | None, max_bytes: int) -> bytes:
    """Change print metadata only; preserve the converted source drawing parts."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > max_bytes:
            raise ExcelRegionRenderError("output_too_large", "转换后的工作簿超过大小限制")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_list = workbook.find(f"{{{_MAIN}}}sheets")
        sheets = list(sheet_list) if sheet_list is not None else []
        selected = [sheet for sheet in sheets if sheet.get("name") == sheet_name]
        if len(selected) != 1:
            raise ExcelRegionRenderError("sheet_binding_failed", "转换后的工作表与请求不一致")
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
        if cell_range is not None:
            area = ET.SubElement(names, f"{{{_MAIN}}}definedName", name="_xlnm.Print_Area", localSheetId=str(index))
            area.text = f"'{quoted}'!{absolute_coordinate(cell_range)}"
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationship_id = target.get(f"{{{_REL}}}id")
        relations = [item for item in relationships if item.get("Id") == relationship_id and item.get("TargetMode") != "External"]
        if len(relations) != 1:
            raise ExcelRegionRenderError("sheet_binding_failed", "无法确认转换后的工作表原件关联")
        relative = relations[0].get("Target", "")
        part = relative.lstrip("/") if relative.startswith("/") else "xl/" + relative
        if ".." in Path(part).parts:
            raise ExcelRegionRenderError("sheet_binding_failed", "工作表关联越界")
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


def _sheet_part(archive, sheet_name):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for sheet in workbook.findall(f"{{{_MAIN}}}sheets/{{{_MAIN}}}sheet"):
        if sheet.get("name") == sheet_name:
            relation = next(item for item in rels if item.get("Id") == sheet.get(f"{{{_REL}}}id") and item.get("TargetMode") != "External")
            target = relation.get("Target", "")
            return target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
    raise ExcelRegionRenderError("sheet_binding_failed", "未找到请求的工作表")


def has_region_graphics(content: bytes, *, sheet_name: str, cell_range: str | None) -> bool:
    left, top, right, bottom = range_boundaries(cell_range) if cell_range is not None else (1, 1, 16384, 1048576)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        part = _sheet_part(archive, sheet_name)
        worksheet = ET.fromstring(archive.read(part))
        if any(node.tag.rsplit("}", 1)[-1] == "control" for node in worksheet.iter()):
            return True
        rel_path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
        if rel_path not in archive.namelist():
            return False
        relations = ET.fromstring(archive.read(rel_path))
        ids = {node.get(f"{{{_REL}}}id") for node in worksheet if node.tag.rsplit("}", 1)[-1] in {"drawing", "legacyDrawing"}}
        for relation in relations:
            if relation.get("Id") not in ids or relation.get("TargetMode") == "External":
                continue
            target = relation.get("Target", "")
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
            drawing = ET.fromstring(archive.read(target))
            for anchor in drawing:
                kind = anchor.tag.rsplit("}", 1)[-1]
                if kind == "absoluteAnchor":
                    return True  # No cell footprint: it cannot prove a field blank.
                if kind == "shape":
                    client = next((node for node in anchor.iter() if node.tag.rsplit("}", 1)[-1] == "ClientData"), None)
                    if client is not None and client.get("ObjectType") == "Note":
                        continue
                    placement = next((node.text for node in anchor.iter() if node.tag.rsplit("}", 1)[-1] == "Anchor"), None)
                    if placement is None:
                        return True
                    positions = [int(value.strip()) for value in placement.split(",")]
                    if len(positions) != 8:
                        return True
                    if positions[2] + 1 <= bottom and positions[6] + 1 >= top and positions[0] + 1 <= right and positions[4] + 1 >= left:
                        return True
                starts: dict[str, int] = {}
                ends: dict[str, int] = {}
                for node in anchor:
                    name = node.tag.rsplit("}", 1)[-1]
                    if name in {"from", "to"}:
                        values = {child.tag.rsplit("}", 1)[-1]: int(child.text or 0) + 1 for child in node if child.tag.rsplit("}", 1)[-1] in {"row", "col"}}
                        (starts if name == "from" else ends).update(values)
                # A one-cell anchor has an extent, not a `to` marker. Until its
                # exact footprint is known it can extend below/right of its
                # anchor; it cannot establish that an overlapping field is blank.
                if starts and starts.get("row", 1) <= bottom and ends.get("row", bottom) >= top and starts.get("col", 1) <= right and ends.get("col", right) >= left:
                    return True
    return False


@lru_cache(maxsize=8)
def renderable_xlsx(content: bytes) -> bytes:
    """LibreOffice is only for pictures of a workbook; cell values are read from the original."""
    if content.startswith(ExcelLoader.OLE_MAGIC):
        return convert_excel(content, source_suffix="xls", target_suffix="xlsx", output_filter="xlsx:Calc MS Excel 2007 XML")
    return content


def render_xlsx_pdf(content: bytes, *, sheet_name: str, cell_range: str | None) -> bytes:
    limits = LegacyDocConversionLimits()
    region = _region_print_copy(content, sheet_name=sheet_name, cell_range=cell_range, max_bytes=limits.max_output_bytes)
    return convert_excel(region, source_suffix="xlsx", target_suffix="pdf", output_filter="pdf:calc_pdf_Export")


def render_xlsx_field(content: bytes, *, sheet_name: str, cell_range: str, keywords: tuple[str, ...]) -> bytes:
    pdf = render_xlsx_pdf(content, sheet_name=sheet_name, cell_range=cell_range)
    with fitz.open(stream=pdf, filetype="pdf") as document:
        if len(document) != 1:
            raise ExcelRegionRenderError("region_coverage_unconfirmed", "字段区域未生成唯一页面")
        page = document[0]
        found = [box for keyword in keywords for box in page.search_for(keyword)]
        if not found:
            raise ExcelRegionRenderError("field_text_unconfirmed", "渲染结果没有找到字段定位文字")
        bounds = fitz.Rect(found[0])
        for box in found[1:]:
            bounds |= box
        height = max(box.height for box in found)
        text_band = fitz.Rect(bounds) + (-height * 3, -height, height * 3, height)
        for drawing in page.get_drawings():
            marker = fitz.Rect(drawing["rect"])
            if marker.intersects(text_band) and 0 < min(marker.width, marker.height) and max(marker.width, marker.height) <= height * 3:
                bounds |= marker
        bounds = bounds + (-height * 3, -height, height, height) & page.rect
        scale = min(4, 4096 / max(bounds.width, bounds.height))
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=bounds, alpha=False).tobytes("png")
