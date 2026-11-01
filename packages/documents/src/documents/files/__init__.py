"""文件处理工具模块

提供文件检测、下载和通用工具：
- detect: 文件类型检测
- download: 文件下载功能
- utils: 通用文件工具
"""

from .detect import (
    detect_file_type,
    detect_file_type_from_filename,
    get_file_extension_from_url,
    get_mime_type_for_extension,
    validate_file_type,
)
from .download import (
    download_file_sync,
    download_file_to_dir_sync,
    download_file_to_temp_sync,
    parse_file_size,
)
from .utils import (
    calculate_file_hash,
    extract_metadata_from_filename,
    format_file_size,
    get_file_extension,
    get_file_mime_type,
    sanitize_filename,
)

__all__ = [
    # 文件类型检测
    "detect_file_type",
    "detect_file_type_from_filename",
    "validate_file_type",
    "get_file_extension_from_url",
    "get_mime_type_for_extension",
    # 文件下载
    "parse_file_size",
    "download_file_sync",
    "download_file_to_temp_sync",
    "download_file_to_dir_sync",
    # 通用工具
    "get_file_extension",
    "get_file_mime_type",
    "calculate_file_hash",
    "format_file_size",
    "sanitize_filename",
    "extract_metadata_from_filename",
]
