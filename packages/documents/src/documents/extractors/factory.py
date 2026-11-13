"""文件内容提取器工厂"""

import logging
from pathlib import Path
from typing import Dict, Type, cast

from .base import BaseExtractor
from .excel import ExcelExtractor
from .image import ImageExtractor
from .markdown import MarkdownExtractor
from .pdf import PDFExtractor
from .powerpoint import PowerPointExtractor
from .word import WordExtractor

logger = logging.getLogger(__name__)


class ExtractorFactory:
    """文件内容提取器工厂类

    负责根据文件类型创建相应的提取器实例。
    支持自动检测文件类型并选择合适的提取器。
    """

    def __init__(self) -> None:
        """初始化提取器工厂"""
        self._extractors: Dict[str, Type[BaseExtractor]] = {}
        self._register_default_extractors()

    def _register_default_extractors(self) -> None:
        """注册默认的提取器"""
        extractors: list[Type[BaseExtractor]] = [
            PDFExtractor,
            ExcelExtractor,
            WordExtractor,
            PowerPointExtractor,
            ImageExtractor,
            cast(Type[BaseExtractor], MarkdownExtractor),
        ]

        for extractor_class in extractors:
            self.register_extractor(extractor_class)

    def register_extractor(self, extractor_class: Type[BaseExtractor]) -> None:
        """注册新的提取器类

        Args:
            extractor_class: 提取器类，必须继承BaseExtractor
        """
        if not issubclass(extractor_class, BaseExtractor):
            raise ValueError(f"提取器类 {extractor_class.__name__} 必须继承 BaseExtractor")

        # 创建临时实例以获取支持的扩展名
        temp_instance = extractor_class()
        supported_extensions = temp_instance.supported_extensions

        for ext in supported_extensions:
            if ext in self._extractors:
                logger.warning(f"文件扩展名 {ext} 已经注册了提取器，将被 {extractor_class.__name__} 覆盖")
            self._extractors[ext.lower()] = extractor_class

        logger.debug(f"已注册提取器 {extractor_class.__name__}，支持扩展名: {supported_extensions}")

    def get_extractor(self, file_extension: str) -> BaseExtractor:
        """根据文件扩展名获取提取器实例

        Args:
            file_extension: 文件扩展名（包含点号，如'.pdf'）

        Returns:
            BaseExtractor: 相应的提取器实例

        Raises:
            ValueError: 当不支持该文件类型时抛出
        """
        ext = file_extension.lower()

        if ext not in self._extractors:
            supported_types = sorted(self._extractors.keys())
            raise ValueError(f"不支持的文件类型: {file_extension}。支持的类型: {supported_types}")

        extractor_class = self._extractors[ext]
        return extractor_class()

    def get_extractor_for_file(self, file_path: str) -> BaseExtractor:
        """根据文件路径获取提取器实例

        Args:
            file_path: 文件路径

        Returns:
            BaseExtractor: 相应的提取器实例
        """
        file_extension = Path(file_path).suffix
        return self.get_extractor(file_extension)

    def is_supported(self, file_extension: str) -> bool:
        """检查是否支持指定的文件类型

        Args:
            file_extension: 文件扩展名（包含点号，如'.pdf'）

        Returns:
            bool: 是否支持该文件类型
        """
        return file_extension.lower() in self._extractors


# 创建全局工厂实例
extractor_factory = ExtractorFactory()


def get_extractor(file_extension: str) -> BaseExtractor:
    """便捷函数：根据文件扩展名获取提取器实例

    Args:
        file_extension: 文件扩展名（包含点号，如'.pdf'）

    Returns:
        BaseExtractor: 相应的提取器实例
    """
    return extractor_factory.get_extractor(file_extension)


def get_extractor_for_file(file_path: str) -> BaseExtractor:
    """便捷函数：根据文件路径获取提取器实例

    Args:
        file_path: 文件路径

    Returns:
        BaseExtractor: 相应的提取器实例
    """
    return extractor_factory.get_extractor_for_file(file_path)


def is_supported_file_type(file_extension: str) -> bool:
    """便捷函数：检查是否支持指定的文件类型

    Args:
        file_extension: 文件扩展名（包含点号，如'.pdf'）

    Returns:
        bool: 是否支持该文件类型
    """
    return extractor_factory.is_supported(file_extension)
