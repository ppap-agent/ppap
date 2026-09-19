"""文件下载工具函数"""

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


def parse_file_size(size_str: str) -> int:
    """解析文件大小字符串，返回字节数

    Args:
        size_str: 文件大小字符串，如 "10MB", "1GB", "500KB"

    Returns:
        int: 文件大小（字节）
    """
    size_str = size_str.upper()
    if size_str.endswith("MB"):
        return int(size_str[:-2]) * 1024 * 1024
    elif size_str.endswith("KB"):
        return int(size_str[:-2]) * 1024
    elif size_str.endswith("GB"):
        return int(size_str[:-2]) * 1024 * 1024 * 1024
    else:
        return int(size_str)


def download_file_sync(url: str, max_file_size: int) -> Tuple[bytes, dict]:
    """同步下载文件

    Args:
        url: 文件URL
        max_file_size: 最大文件大小（字节）

    Returns:
        Tuple[bytes, dict]: (文件内容, 响应头信息)

    Raises:
        ValueError: 下载失败或文件太大时抛出
    """
    max_retries = 3
    last_exception: Exception | None = None

    for attempt in range(max_retries):
        should_retry = False

        try:
            response = requests.get(url, timeout=300)
            response.raise_for_status()

            # 检查文件大小
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_file_size:
                raise ValueError(f"文件太大，超过了{max_file_size}字节的限制")

            file_content = response.content
            if len(file_content) > max_file_size:
                raise ValueError(f"文件太大，超过了{max_file_size}字节的限制")

            return file_content, dict(response.headers)

        except requests.exceptions.Timeout as e:
            last_exception = e
            should_retry = True
            logger.warning(f"下载超时，尝试 {attempt + 1}/{max_retries}: {url}")

        except requests.exceptions.HTTPError as e:
            # HTTP错误通常不需要重试（如404, 403等）
            raise ValueError(f"文件下载失败: HTTP {e.response.status_code}")

        except requests.exceptions.RequestException as e:
            last_exception = e
            should_retry = True
            logger.warning(f"网络请求异常，尝试 {attempt + 1}/{max_retries}: {str(e)}")

        except ValueError:
            raise

        except Exception as e:
            last_exception = e
            should_retry = True
            logger.warning(f"下载异常，尝试 {attempt + 1}/{max_retries}: {str(e)}")

        finally:
            if should_retry and attempt < max_retries - 1:
                time.sleep(2**attempt)  # 指数退避：2, 4秒

    # 所有重试都失败了
    if isinstance(last_exception, requests.exceptions.Timeout):
        raise ValueError("，文件下载超时")
    else:
        raise ValueError(f"重试后，文件下载失败: {str(last_exception)}")


def download_file_to_dir_sync(
    url: str,
    dest_dir: Union[str, Path],
    max_file_size: int,
    custom_filename: Optional[str] = None,
) -> str:
    """
    同步下载文件并保存到本地目录

    Args:
        url: 要下载的文件 URL
        dest_dir: 本地保存目录路径
        max_file_size: 最大文件大小（字节）
        custom_filename: 自定义文件名，如果不提供则从URL解析

    Returns:
        str: 保存到本地后的文件完整路径

    Raises:
        ValueError: 下载失败或文件太大时抛出
    """
    file_content, headers = download_file_sync(url, max_file_size)

    # 确定文件名
    if custom_filename:
        file_name = custom_filename
    else:
        # 从 URL 解析出文件名
        raw_path = urlparse(url).path
        file_name = os.path.basename(raw_path)
        if not file_name:
            file_name = f"file_{int(time.time())}"

    # 确保目标目录存在
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 本地文件完整路径
    file_path = dest_dir / file_name

    # 同步写文件
    with open(file_path, "wb") as f:
        f.write(file_content)

    return str(file_path)


def download_file_to_temp_sync(
    url: str,
    dest_dir: Union[str, Path],
    max_file_size: int,
) -> str:
    """
    同步下载文件并保存到目标目录下的唯一临时文件

    Args:
        url: 要下载的文件 URL
        dest_dir: 本地保存目录路径
        max_file_size: 最大文件大小（字节）

    Returns:
        str: 保存到本地后的文件完整路径

    Raises:
        ValueError: 下载失败或文件太大时抛出
    """
    file_content, headers = download_file_sync(url, max_file_size)
    del headers

    raw_path = urlparse(url).path
    suffix = Path(os.path.basename(raw_path)).suffix or ""

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dest_dir, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(file_content)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.debug("cleanup failed for temp download: %s", tmp_path)
        raise

    return str(tmp_path)
