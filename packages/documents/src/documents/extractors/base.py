"""基础文件内容提取器接口"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Union


class BaseExtractor(ABC):
    """基础文件内容提取器抽象类

    定义了所有文件内容提取器的通用接口。
    所有具体的提取器都应该继承此类并实现相应的方法。
    """

    @abstractmethod
    def extract_text(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> str:
        """提取文件的文本内容

        Args:
            file_content: 文件的二进制内容
            file_path: 可选的文件路径，用于某些需要文件扩展名的提取器

        Returns:
            str: 提取的文本内容

        Raises:
            ExtractionError: 当提取失败时抛出
        """

    def extract_metadata(self, file_content: bytes, file_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """提取文件的元数据（可选实现）

        Args:
            file_content: 文件的二进制内容
            file_path: 可选的文件路径

        Returns:
            Dict[str, Any]: 包含文件元数据的字典
        """
        return {}

    def is_supported(self, file_extension: str) -> bool:
        """检查是否支持指定的文件扩展名

        Args:
            file_extension: 文件扩展名（包含点号，如'.pdf'）

        Returns:
            bool: 是否支持该文件类型
        """
        return file_extension.lower() in self.supported_extensions

    @property
    @abstractmethod
    def supported_extensions(self) -> set[str]:
        """返回支持的文件扩展名集合"""


class ExtractionError(Exception):
    """文件内容提取异常"""

    def __init__(
        self,
        message: str,
        file_type: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.message = message
        self.file_type = file_type
        self.original_error = original_error
        super().__init__(self.message)

    def __str__(self) -> str:
        error_msg = f"文件内容提取失败: {self.message}"
        if self.file_type:
            error_msg += f" (文件类型: {self.file_type})"
        if self.original_error:
            error_msg += f" (原始错误: {str(self.original_error)})"
        return error_msg
