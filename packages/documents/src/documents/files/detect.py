"""文件类型检测工具函数"""

import logging
from pathlib import Path
from typing import Optional, Set
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


def detect_file_type_from_filename(filename: str, allowed_extensions: Optional[Set[str]] = None) -> str:
    """快速从文件名检测文件类型（高性能版本）

    这个函数只检查文件名后缀，不检查文件内容，适用于性能敏感的场景。

    Args:
        filename: 文件名或文件路径
        allowed_extensions: 允许的文件扩展名集合（可选）

    Returns:
        str: 检测到的文件扩展名，如果不支持则返回 '.unknown'
    """
    # 默认允许的扩展名
    if allowed_extensions is None:
        allowed_extensions = {
            ".pdf",
            ".xlsx",
            ".xls",
            ".xlsm",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".tiff",
            ".tif",
            ".md",
        }

    try:
        extension = Path(filename).suffix.lower()
        return extension if extension in allowed_extensions else ".unknown"
    except Exception:
        return ".unknown"


def detect_file_type(
    file_content: bytes,
    url: str = "",
    content_type: str = "",
    allowed_extensions: Optional[Set[str]] = None,
) -> str:
    """检测文件类型

    Args:
        file_content: 文件二进制内容
        url: 文件URL（可选）
        content_type: HTTP Content-Type头（可选）
        allowed_extensions: 允许的文件扩展名集合（可选）

    Returns:
        str: 检测到的文件扩展名（如 '.pdf', '.xlsx' 等），如果无法检测则返回 '.unknown'
    """
    # 默认允许的扩展名
    if allowed_extensions is None:
        allowed_extensions = {
            ".pdf",
            ".xlsx",
            ".xls",
            ".xlsm",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".tiff",
            ".tif",
            ".md",
        }

    # 优先从URL文件名后缀检测（最高效的方法）
    if url:
        parsed_url = Path(url)
        extension = parsed_url.suffix.lower()
        if extension in allowed_extensions:
            return extension

    # 次优选择：从Content-Type检测
    if content_type:
        content_type_lower = content_type.lower()

        if "pdf" in content_type_lower:
            return ".pdf"
        elif "wordprocessingml" in content_type_lower or "msword" in content_type_lower or (("word" in content_type_lower or "document" in content_type_lower) and "excel" not in content_type_lower):
            return ".docx"
        elif "spreadsheetml" in content_type_lower or "ms-excel" in content_type_lower or "excel" in content_type_lower or "spreadsheet" in content_type_lower:
            return ".xlsx"
        elif "presentationml" in content_type_lower or "ms-powerpoint" in content_type_lower or "presentation" in content_type_lower or "powerpoint" in content_type_lower:
            return ".pptx"
        elif "markdown" in content_type_lower or "text/x-markdown" in content_type_lower:
            return ".md"
        elif "image" in content_type_lower:
            if "jpeg" in content_type_lower:
                return ".jpg"
            elif "png" in content_type_lower:
                return ".png"
            elif "gif" in content_type_lower:
                return ".gif"
            elif "bmp" in content_type_lower:
                return ".bmp"
            elif "webp" in content_type_lower:
                return ".webp"
            elif "tiff" in content_type_lower:
                return ".tiff"

    # 最后手段：检查文件内容（仅在前两种方法失败时使用）
    if file_content:
        logger.debug("正在通过文件内容检测文件类型（性能较低的方法）")

        # PDF文件头
        if file_content.startswith(b"%PDF"):
            return ".pdf"
        # Office文件头（ZIP格式）
        elif file_content.startswith(b"PK"):
            # 更精确的Office文件检测 - 扩大搜索范围到前4KB
            search_content = file_content[:4096] if len(file_content) >= 4096 else file_content

            # 检查是否包含Word相关标识
            if b"word/" in search_content or b"document.xml" in search_content or b"[Content_Types].xml" in search_content and b"wordprocessingml" in search_content:
                return ".docx"
            # 检查是否包含PowerPoint相关标识
            elif b"ppt/" in search_content or b"presentation.xml" in search_content or b"[Content_Types].xml" in search_content and b"presentationml" in search_content:
                return ".pptx"
            # 检查是否包含Excel相关标识
            elif b"xl/" in search_content or b"workbook.xml" in search_content or b"[Content_Types].xml" in search_content and b"spreadsheetml" in search_content:
                return ".xlsx"
            else:
                # 默认返回.docx（因为大多数Office文档是Word）
                return ".docx"
        # 旧版Office文件头（OLE格式）
        elif file_content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            # 简单检测，默认返回.xls
            return ".xls"
        # JPEG文件头
        elif file_content.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        # PNG文件头
        elif file_content.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        # GIF文件头
        elif file_content.startswith(b"GIF87a") or file_content.startswith(b"GIF89a"):
            return ".gif"
        # BMP文件头
        elif file_content.startswith(b"BM"):
            return ".bmp"
        # TIFF文件头
        elif file_content.startswith(b"II*\x00") or file_content.startswith(b"MM\x00*"):
            return ".tiff"
        # WebP文件头
        elif file_content.startswith(b"RIFF") and b"WEBP" in file_content[:12]:
            return ".webp"

    return ".unknown"


def validate_file_type(file_type: str, allowed_extensions: Optional[Set[str]] = None) -> bool:
    """验证文件类型是否支持

    Args:
        file_type: 文件类型/扩展名
        allowed_extensions: 允许的文件扩展名集合（可选）

    Returns:
        bool: 是否支持该文件类型
    """
    # 默认允许的扩展名
    if allowed_extensions is None:
        allowed_extensions = {
            ".pdf",
            ".xlsx",
            ".xls",
            ".xlsm",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".tiff",
            ".tif",
            ".md",
        }

    return file_type.lower() in allowed_extensions


def get_file_extension_from_url(url: str) -> str:
    """从URL中提取文件扩展名

    Args:
        url: 文件URL

    Returns:
        str: 文件扩展名（小写，包含前面的 '.', e.g. '.txt'），如果无法提取则返回空字符串
    """
    try:
        parsed_url = urlparse(url)
        return Path(unquote(parsed_url.path)).suffix.lower()
    except Exception:
        return ""


def get_mime_type_for_extension(extension: str) -> str:
    """根据文件扩展名获取MIME类型

    Args:
        extension: 文件扩展名

    Returns:
        str: MIME类型字符串
    """
    mime_types = {
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xls": "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
        ".md": "text/markdown",
    }

    return mime_types.get(extension.lower(), "application/octet-stream")
