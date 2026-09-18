"""PowerPoint文档内容提取器"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class PowerPointExtractor(BaseExtractor):
    """PowerPoint文档内容提取器"""

    @property
    def supported_extensions(self) -> Set[str]:
        return {".pptx", ".ppt"}

    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """提取PowerPoint文档的文本内容

        Args:
            file_content: PowerPoint文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            str: 提取的文本内容

        Raises:
            ExtractionError: 当PowerPoint解析失败时抛出
        """
        try:
            # 直接使用 python-pptx 提取文本
            import io

            from pptx import Presentation

            logger.debug(f"开始提取PowerPoint文本内容，文件大小: {len(file_content)} bytes")

            # 使用 python-pptx 处理
            presentation = Presentation(io.BytesIO(file_content))
            text_content = []

            for slide in presentation.slides:
                slide_text = []
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        if shape.text.strip():
                            slide_text.append(shape.text.strip())

                if slide_text:
                    text_content.append("\n".join(slide_text))

            result = "\n\n".join(text_content)

            if not result:
                logger.warning("PowerPoint文档中未提取到文本内容")
                return ""

            logger.debug(f"PowerPoint文本提取成功，内容长度: {len(result)} 字符")
            return result

        except ImportError as e:
            raise ExtractionError("PowerPoint提取器依赖库未安装", file_type="PowerPoint", original_error=e)
        except Exception as e:
            raise ExtractionError(
                f"PowerPoint文本提取失败: {str(e)}",
                file_type="PowerPoint",
                original_error=e,
            )

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取PowerPoint文档的元数据

        Args:
            file_content: PowerPoint文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            Dict[str, Any]: 包含PowerPoint元数据的字典
        """
        try:
            import io
            import zipfile
            from xml.etree import ElementTree as ET

            metadata = {"file_size": len(file_content), "format": "PowerPoint"}

            # 尝试从.pptx文件中提取元数据
            if file_path and str(file_path).lower().endswith(".pptx"):
                try:
                    with zipfile.ZipFile(io.BytesIO(file_content), "r") as pptx_zip:
                        # 读取核心属性
                        if "docProps/core.xml" in pptx_zip.namelist():
                            core_xml = pptx_zip.read("docProps/core.xml")
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

                            created = root.find(".//dcterms:created", namespaces)
                            if created is not None:
                                metadata["creation_date"] = created.text or ""

                            modified = root.find(".//dcterms:modified", namespaces)
                            if modified is not None:
                                metadata["modification_date"] = modified.text or ""

                        # 统计幻灯片数量
                        slide_files = [f for f in pptx_zip.namelist() if f.startswith("ppt/slides/slide") and f.endswith(".xml")]
                        metadata["slide_count"] = len(slide_files)

                except Exception as e:
                    logger.warning(f"无法从PowerPoint文档中提取详细元数据: {str(e)}")

            return metadata

        except Exception as e:
            logger.error(f"PowerPoint元数据提取失败: {str(e)}")
            return {
                "format": "PowerPoint",
                "file_size": len(file_content),
                "error": str(e),
            }
