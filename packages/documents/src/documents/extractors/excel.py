"""Excel文件内容提取器"""

import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union
from xml.etree.ElementTree import Element
from xml.sax.saxutils import escape, quoteattr

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.utils.datetime import from_excel, from_ISO8601
from openpyxl.xml.functions import iterparse

from ..excel.loader import ExcelLoader, ExcelLoadError, ExcelLoadOptions
from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)

_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_CELL_TAG = f"{{{_SPREADSHEET_NS}}}c"
_INLINE_STRING_TAG = f"{{{_SPREADSHEET_NS}}}is"
_ROW_TAG = f"{{{_SPREADSHEET_NS}}}row"
_TEXT_TAG = f"{{{_SPREADSHEET_NS}}}t"
_VALUE_TAG = f"{{{_SPREADSHEET_NS}}}v"


class ExcelExtractor(BaseExtractor):
    """Excel文件内容提取器"""

    @property
    def supported_extensions(self) -> Set[str]:
        return {".xlsx", ".xls", ".xlsm"}

    @staticmethod
    def _cast_number(value: str) -> int | float:
        if "." not in value and "E" not in value and "e" not in value:
            return int(value)
        return float(value)

    @classmethod
    def _parse_xml_cell(
        cls,
        element: Element,
        *,
        row_index: int,
        previous_column: int,
        sheet: Any,
    ) -> dict[str, Any]:
        coordinate = element.get("r")
        if coordinate:
            parsed_row, column = coordinate_to_tuple(coordinate)
        else:
            parsed_row, column = row_index, previous_column + 1

        data_type = element.get("t", "n")
        raw_value = element.findtext(_VALUE_TAG) or None
        value: Any = None
        if data_type == "inlineStr":
            inline_string = element.find(_INLINE_STRING_TAG)
            if inline_string is not None:
                value = "".join(text.text or "" for text in inline_string.iter(_TEXT_TAG))
        elif raw_value is not None:
            if data_type == "n":
                value = cls._cast_number(raw_value)
                style_id = int(element.get("s", "0"))
                parent = sheet.parent
                if style_id in getattr(parent, "_date_formats", set()):
                    try:
                        value = from_excel(
                            value,
                            parent.epoch,
                            timedelta=style_id in getattr(parent, "_timedelta_formats", set()),
                        )
                    except (OverflowError, ValueError):
                        value = "#VALUE!"
            elif data_type == "s":
                value = getattr(sheet, "_shared_strings")[int(raw_value)]
            elif data_type == "b":
                value = bool(int(raw_value))
            elif data_type == "d":
                value = from_ISO8601(raw_value)
            else:
                value = raw_value

        return {"row": parsed_row, "column": column, "value": value}

    @classmethod
    def _iter_xml_rows(cls, sheet: Any) -> Iterator[tuple[int, list[dict[str, Any]]]]:
        archive = getattr(sheet.parent, "_archive")
        source = archive.open(getattr(sheet, "_worksheet_path"))
        try:
            inferred_row = 0
            for _, element in iterparse(source, events=("end",)):
                if element.tag != _ROW_TAG:
                    continue
                inferred_row += 1
                row_index = int(element.get("r", inferred_row))
                inferred_row = row_index
                previous_column = 0
                populated = []
                for cell_element in element.findall(_CELL_TAG):
                    cell = cls._parse_xml_cell(
                        cell_element,
                        row_index=row_index,
                        previous_column=previous_column,
                        sheet=sheet,
                    )
                    previous_column = int(cell["column"])
                    if cell["value"] is not None:
                        populated.append(cell)
                element.clear()
                if populated:
                    yield int(populated[0]["row"]), populated
        finally:
            source.close()

    @classmethod
    def _iter_populated_rows(cls, sheet: Any) -> Iterator[tuple[int, list[dict[str, Any]]]]:
        if getattr(sheet.parent, "read_only", False):
            yield from cls._iter_xml_rows(sheet)
            return

        for row in sheet.iter_rows(values_only=False):
            populated = [
                {
                    "row": cell.row,
                    "column": cell.column,
                    "value": cell.value,
                }
                for cell in row
                if cell.value is not None
            ]
            if populated:
                yield populated[0]["row"], populated

    @classmethod
    def _format_sheet_text(cls, sheet: Any) -> str:
        lines = [f"<SHEET name={quoteattr(str(sheet.title))}>"]
        for row_index, cells in cls._iter_populated_rows(sheet):
            lines.append(f'  <ROW index="{row_index}">')
            for cell in cells:
                column = int(cell["column"])
                address = f"{get_column_letter(column)}{cell['row']}"
                value = str(cell["value"])
                lines.append(f'    <CELL index="{column}" address="{address}">{escape(value)}</CELL>')
            lines.append("  </ROW>")
        lines.append("</SHEET>")
        return "\n".join(lines) if len(lines) > 2 else ""

    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """提取Excel文档的文本内容

        Args:
            file_content: Excel文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            str: 提取的文本内容

        Raises:
            ExtractionError: 当Excel解析失败时抛出
        """
        try:
            logger.debug(f"开始提取Excel文本内容，文件大小: {len(file_content)} bytes")

            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            try:
                # 使用ExcelLoader加载工作簿
                workbook = ExcelLoader.load_workbook(temp_file_path, ExcelLoadOptions(read_only=True))
                try:
                    # 提取所有工作表的原始行列文本内容
                    text_content = []
                    for sheet in workbook.worksheets:
                        sheet_text = self._format_sheet_text(sheet)
                        if sheet_text:
                            text_content.append(sheet_text)

                    result = "\n\n".join(text_content)
                finally:
                    workbook.close()

            finally:
                # 清理临时文件
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

            if not result:
                logger.warning("Excel文档中未提取到文本内容")
                return ""

            logger.debug(f"Excel文本提取成功，内容长度: {len(result)} 字符")
            return result

        except ExcelLoadError as e:
            raise ExtractionError(f"Excel文件加载失败: {str(e)}", file_type="Excel", original_error=e)
        except Exception as e:
            raise ExtractionError(f"Excel文本提取失败: {str(e)}", file_type="Excel", original_error=e)

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取Excel文档的元数据

        Args:
            file_content: Excel文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            Dict[str, Any]: 包含Excel元数据的字典
        """
        try:
            metadata = {"file_size": len(file_content), "format": "Excel"}

            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name

            try:
                # 使用ExcelLoader加载工作簿
                workbook = ExcelLoader.load_workbook(temp_file_path, ExcelLoadOptions(read_only=True))
                try:
                    # 提取工作表信息
                    sheet_names = [sheet.title for sheet in workbook.worksheets]
                    metadata["sheet_names"] = sheet_names
                    metadata["sheet_count"] = len(sheet_names)

                    # 只使用实际有值的单元格计算边界，忽略不可信的 worksheet dimension。
                    sheets_info = []
                    for sheet in workbook.worksheets:
                        try:
                            max_row = 0
                            max_column = 0
                            for _, cells in self._iter_populated_rows(sheet):
                                row_max_row = max(int(cell["row"]) for cell in cells)
                                row_max_column = max(int(cell["column"]) for cell in cells)
                                max_row = max(max_row, row_max_row)
                                max_column = max(max_column, row_max_column)
                            sheets_info.append(
                                {
                                    "name": sheet.title,
                                    "max_row": max_row,
                                    "max_column": max_column,
                                }
                            )
                        except Exception as e:
                            logger.warning(f"无法获取工作表 {sheet.title} 的信息: {str(e)}")

                    metadata["sheets_info"] = sheets_info
                finally:
                    workbook.close()

            finally:
                # 清理临时文件
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)

            return metadata

        except Exception as e:
            logger.error(f"Excel元数据提取失败: {str(e)}")
            return {"format": "Excel", "file_size": len(file_content), "error": str(e)}
