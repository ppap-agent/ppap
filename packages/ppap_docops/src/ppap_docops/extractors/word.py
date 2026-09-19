"""Word文档内容提取器"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from xml.sax.saxutils import escape

from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class WordExtractor(BaseExtractor):
    """Word文档内容提取器"""

    @property
    def supported_extensions(self) -> Set[str]:
        return {".docx", ".doc"}

    @staticmethod
    def _xml_cell_paragraphs(cell: Any, ns: Dict[str, str]) -> List[str]:
        paragraphs: List[str] = []
        for paragraph in cell.findall(".//w:p", ns):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
            if text:
                paragraphs.append(text)
        return paragraphs

    @classmethod
    def _format_raw_table(cls, rows: List[List[List[str]]], table_index: int) -> str:
        lines = [f'<TABLE index="{table_index}">']
        for row_index, row in enumerate(rows, start=1):
            lines.append(f'  <ROW index="{row_index}">')
            for col_index, paragraphs in enumerate(row, start=1):
                lines.append(f'    <CELL index="{col_index}">')
                for paragraph in paragraphs:
                    lines.append(f"      <P>{escape(paragraph)}</P>")
                lines.append("    </CELL>")
            lines.append("  </ROW>")
        lines.append("</TABLE>")
        return "\n".join(lines)

    @classmethod
    def _extract_docx_tables(cls, file_content: bytes) -> List[str] | None:
        try:
            import zipfile
            from io import BytesIO
            from xml.etree import ElementTree as ET

            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            with zipfile.ZipFile(BytesIO(file_content), "r") as docx_zip:
                if "word/document.xml" not in docx_zip.namelist():
                    return None
                root = ET.fromstring(docx_zip.read("word/document.xml"))

            table_texts: List[str] = []
            for table_index, table in enumerate(root.findall(".//w:tbl", ns), start=1):
                xml_rows = table.findall("./w:tr", ns)
                rows = [[cls._xml_cell_paragraphs(cell, ns) for cell in xml_row.findall("./w:tc", ns)] for xml_row in xml_rows]
                if not rows:
                    continue

                table_text = cls._format_raw_table(rows, table_index)
                if table_text:
                    table_texts.append(table_text)
            return table_texts
        except Exception as exc:
            logger.debug("Word XML 表格提取失败，回退到 python-docx 表格提取: %s", exc)
            return None

    @classmethod
    def _extract_tables_with_python_docx(cls, doc: Any) -> List[str]:
        table_texts: List[str] = []
        for table in doc.tables:
            rows = [[[paragraph.text.strip() for paragraph in cell.paragraphs if paragraph.text.strip()] for cell in row.cells] for row in table.rows]
            if rows:
                table_text = cls._format_raw_table(rows, len(table_texts) + 1)
                if table_text:
                    table_texts.append(table_text)
        return table_texts

    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """提取Word文档的文本内容

        Args:
            file_content: Word文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            str: 提取的文本内容

        Raises:
            ExtractionError: 当Word解析失败时抛出
        """
        try:
            from io import BytesIO

            from docx import Document

            logger.debug(f"开始提取Word文本内容，文件大小: {len(file_content)} bytes")
            doc = Document(BytesIO(file_content))

            text_parts = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]
            table_texts = self._extract_docx_tables(file_content)
            text_parts.extend(table_texts if table_texts is not None else self._extract_tables_with_python_docx(doc))

            text_content = "\n\n".join(text_parts)
            if not text_content:
                logger.warning("Word文档中未提取到文本内容")
                return ""

            logger.debug(f"Word文本提取成功，内容长度: {len(text_content)} 字符")
            return text_content

        except ImportError as e:
            raise ExtractionError("Word提取器依赖库未安装", file_type="Word", original_error=e)
        except Exception as e:
            raise ExtractionError(f"Word文本提取失败: {str(e)}", file_type="Word", original_error=e)

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取Word文档的元数据

        Args:
            file_content: Word文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            Dict[str, Any]: 包含Word元数据的字典
        """
        try:
            import io
            import zipfile
            from xml.etree import ElementTree as ET

            metadata = {"file_size": len(file_content), "format": "Word"}

            # 尝试从.docx文件中提取元数据
            if file_path and str(file_path).lower().endswith(".docx"):
                try:
                    with zipfile.ZipFile(io.BytesIO(file_content), "r") as docx_zip:
                        # 读取核心属性
                        if "docProps/core.xml" in docx_zip.namelist():
                            core_xml = docx_zip.read("docProps/core.xml")
                            root = ET.fromstring(core_xml)

                            # 定义命名空间
                            namespaces = {
                                "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                                "dc": "http://purl.org/dc/elements/1.1/",
                                "dcterms": "http://purl.org/dc/terms/",
                            }

                            # 提取属性
                            title = root.find(".//dc:title", namespaces)
                            if title is not None:
                                metadata["title"] = title.text or ""

                            creator = root.find(".//dc:creator", namespaces)
                            if creator is not None:
                                metadata["author"] = creator.text or ""

                            subject = root.find(".//dc:subject", namespaces)
                            if subject is not None:
                                metadata["subject"] = subject.text or ""

                            created = root.find(".//dcterms:created", namespaces)
                            if created is not None:
                                metadata["creation_date"] = created.text or ""

                            modified = root.find(".//dcterms:modified", namespaces)
                            if modified is not None:
                                metadata["modification_date"] = modified.text or ""

                except Exception as e:
                    logger.warning(f"无法从Word文档中提取详细元数据: {str(e)}")

            return metadata

        except Exception as e:
            logger.error(f"Word元数据提取失败: {str(e)}")
            return {"format": "Word", "file_size": len(file_content), "error": str(e)}
