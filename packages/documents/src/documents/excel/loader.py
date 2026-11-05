"""Load every Excel format through the XLSX reader; binary XLS uses LibreOffice."""

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

import openpyxl
import openpyxl.utils.exceptions
from openpyxl import Workbook

logger = logging.getLogger(__name__)


class ExcelLoadError(Exception):
    """Excel 加载失败（损坏、格式不符或不支持）"""


@dataclass(frozen=True)
class ExcelLoadOptions:
    data_only: bool = True
    read_only: bool = False
    keep_vba: bool = False


class ExcelLoader:
    """自动识别真实格式并返回 openpyxl.Workbook；支持 .xlsx/.xlsm/.xltx/.xltm 与 .xls（经转换）"""

    ZIP_MAGIC = b"PK\x03\x04"
    OLE_MAGIC = b"\xd0\xcf\x11\xe0"

    @staticmethod
    def _read_head(src: str | Path | BinaryIO, n: int = 8) -> bytes:
        if isinstance(src, (str, Path)):
            p = Path(src)
            if not p.exists() or not p.is_file():
                raise ExcelLoadError(f"文件不存在: {p}")
            with p.open("rb") as f:
                return f.read(n)
        pos = src.tell()
        head = src.read(n)
        src.seek(pos)
        return head

    @staticmethod
    def _is_zip_excel(src: str | Path | BinaryIO) -> bool:
        head = ExcelLoader._read_head(src, 4)
        if head != ExcelLoader.ZIP_MAGIC:
            return False
        if isinstance(src, (str, Path)):
            return zipfile.is_zipfile(str(src))
        pos = src.tell()
        data = src.read()
        src.seek(pos)
        return zipfile.is_zipfile(io.BytesIO(data))

    @staticmethod
    def _is_ole_xls(src: str | Path | BinaryIO) -> bool:
        return ExcelLoader._read_head(src, 4) == ExcelLoader.OLE_MAGIC

    @staticmethod
    def _attach_open_stream(workbook: Workbook, stream: BinaryIO) -> Workbook:
        """Keep source stream alive for deferred readers (e.g., worksheet images)."""
        setattr(workbook, "_ppap_open_stream", stream)
        close_method = getattr(workbook, "close", None)
        if callable(close_method):
            stream_closed = False

            def _close_with_stream() -> None:
                nonlocal stream_closed
                try:
                    close_method()
                finally:
                    if not stream_closed:
                        stream.close()
                        stream_closed = True

            setattr(workbook, "close", _close_with_stream)
        return workbook

    @classmethod
    def load_workbook(
        cls,
        file_path_or_obj: str | Path | BinaryIO,
        options: ExcelLoadOptions = ExcelLoadOptions(),
    ) -> Workbook:
        src = file_path_or_obj
        try:
            if cls._is_ole_xls(src):
                from .legacy import convert_excel

                if isinstance(src, (str, Path)):
                    original = Path(src).read_bytes()
                else:
                    position = src.tell()
                    src.seek(0)
                    original = src.read()
                    src.seek(position)
                converted = convert_excel(original, source_suffix="xls", target_suffix="xlsx", output_filter="xlsx:Calc MS Excel 2007 XML")
                workbook = cls.load_workbook(io.BytesIO(converted), options=options)
                workbook._ppap_xlsx_content = converted
                return workbook
            # ZIP 型（xlsx/xlsm/xltx/xltm）
            if cls._is_zip_excel(src):
                try:
                    if isinstance(src, (str, Path)):
                        # 非 read_only 模式下，openpyxl 可能不会及时释放按路径打开的底层句柄；
                        # 这里保持文件流存活到 workbook.close，避免图片等延迟读取资源因源流提前关闭而失效。
                        if not options.read_only:
                            stream = Path(src).open("rb")
                            try:
                                workbook = openpyxl.load_workbook(
                                    filename=stream,
                                    data_only=options.data_only,
                                    read_only=options.read_only,
                                    keep_vba=options.keep_vba,
                                )
                            except Exception:
                                stream.close()
                                raise
                            return cls._attach_open_stream(workbook, stream)
                        return openpyxl.load_workbook(
                            filename=str(src),
                            data_only=options.data_only,
                            read_only=options.read_only,
                            keep_vba=options.keep_vba,
                        )
                    return openpyxl.load_workbook(
                        filename=src,
                        data_only=options.data_only,
                        read_only=options.read_only,
                        keep_vba=options.keep_vba,
                    )
                except zipfile.BadZipFile as e:
                    logger.warning(f"zipfile.BadZipFile as {e}")
                    raise ExcelLoadError("Excel 文件读取失败")
                except openpyxl.utils.exceptions.InvalidFileException as e:
                    logger.warning(f"openpyxl.utils.exceptions.InvalidFileException as {e}")
                    raise ExcelLoadError("Excel 文件读取失败")
                except Exception as e:
                    logger.warning(f"openpyxl 加载失败: {e}")
                    raise ExcelLoadError("Excel 文件读取失败")

            # 未识别的文件头
            raise ExcelLoadError("无法识别的文件头：可能扩展名错误、文件为空或已损坏。")

        except ExcelLoadError:
            raise
        except Exception as e:
            raise ExcelLoadError(f"加载 Excel 失败：{e}") from e


def load_excel_workbook(file_path: str) -> Workbook:
    """
    便捷函数：加载Excel工作簿

    Args:
        file_path: Excel文件路径

    Returns:
        workbook对象

    Raises:
        Exception: 当文件无法加载时
    """
    temp_download_path: str | None = None
    if file_path.startswith("http"):
        # 对于远程文件，这里需要先下载到本地
        from documents.files import download_file_to_temp_sync, parse_file_size
        from documents.ocr.env import max_file_size as configured_max_file_size

        max_file_size = parse_file_size(configured_max_file_size())

        downloads_dir = Path("./downloads")
        downloads_dir.mkdir(parents=True, exist_ok=True)

        temp_download_path = download_file_to_temp_sync(file_path, downloads_dir, max_file_size)
        file_path = cast(str, temp_download_path)
    try:
        workbook = ExcelLoader.load_workbook(file_path)
        try:
            if temp_download_path:
                setattr(workbook, "_ppap_source_bytes", Path(temp_download_path).read_bytes())
            else:
                source_path = Path(file_path)
                if source_path.exists():
                    setattr(workbook, "_ppap_source_path", str(source_path.resolve()))
        except OSError as exc:
            logger.debug("附加工作簿源文件信息失败: %s", exc)
        return workbook
    except Exception as e:
        logger.error(f"无法加载文件 {file_path}: {e}")
        raise Exception(f"无法加载文件: {e}")
    finally:
        if temp_download_path:
            try:
                os.unlink(temp_download_path)
            except OSError:
                logger.debug("cleanup failed for temp workbook download: %s", temp_download_path)
