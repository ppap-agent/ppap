import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Optional


def get_file_extension(filename: str) -> str:
    """获取文件扩展名"""
    return Path(filename).suffix.lower()


def get_file_mime_type(filename: str) -> Optional[str]:
    """获取文件MIME类型"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type


def calculate_file_hash(file_content: bytes) -> str:
    """计算文件SHA256哈希值"""
    return hashlib.sha256(file_content).hexdigest()


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小显示"""
    if size_bytes == 0:
        return "0B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes //= 1024
        i += 1

    return f"{size_bytes:.2f}{size_names[i]}"


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除不安全的字符"""
    # 移除路径分隔符和其他不安全字符
    unsafe_chars = ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]
    for char in unsafe_chars:
        filename = filename.replace(char, "_")

    # 限制长度
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[: 255 - len(ext)] + ext

    return filename


def extract_metadata_from_filename(filename: str) -> dict:
    """从文件名中提取元数据"""
    # 简单的文件名解析逻辑
    metadata = {
        "original_name": filename,
        "extension": get_file_extension(filename),
        "mime_type": get_file_mime_type(filename),
        "size_estimate": None,
    }

    # 尝试从文件名中提取更多信息
    # 这里可以根据具体的文件命名规范来实现

    return metadata
