"""Markdown 文本提取器。"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set, Union

from .base import BaseExtractor, ExtractionError

logger = logging.getLogger(__name__)


class MarkdownExtractor(BaseExtractor):
    """Markdown 文本提取器。"""

    @property
    def supported_extensions(self) -> Set[str]:
        return {".md", ".markdown"}

    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """提取 Markdown 文本内容。"""
        encodings = ("utf-8-sig", "utf-8", "gb18030", "gbk")
        for encoding in encodings:
            try:
                return file_content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ExtractionError("Markdown 文本解码失败", file_type="Markdown")

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取 Markdown 基础元数据。"""
        try:
            text = self.extract_text(file_content, file_path)
            lines = text.splitlines()
            heading_count = sum(1 for line in lines if line.lstrip().startswith("#"))
            return {
                "file_size": len(file_content),
                "format": "Markdown",
                "line_count": len(lines),
                "heading_count": heading_count,
            }
        except ExtractionError:
            raise
        except Exception as exc:
            logger.warning("Markdown 元数据提取失败: %s", exc)
            return {"file_size": len(file_content), "format": "Markdown"}
