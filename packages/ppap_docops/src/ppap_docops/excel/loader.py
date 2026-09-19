"""
Excel文件加载器工具
支持.xlsx和.xls格式，提供统一的接口
"""

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

import openpyxl
import openpyxl.utils
import openpyxl.utils.exceptions
import xlrd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger(__name__)

BorderStyle = Literal[
    "dashDot",
    "dashDotDot",
    "dashed",
    "dotted",
    "double",
    "hair",
    "medium",
    "mediumDashDot",
    "mediumDashDotDot",
    "mediumDashed",
    "slantDashDot",
    "thick",
    "thin",
]


logger = logging.getLogger(__name__)


def _xl_color_to_hex(book: Any, color_index: int | None) -> str | None:
    """把 xlrd 的调色板索引转 #RRGGBB；未知或自动色返回 None"""
    if color_index is None:
        return None
    # xlrd: 0x7FFF/32767 表示自动色；有些索引可能不在调色板中
    if color_index in (0x7FFF, 32767):
        return None
    cmap = getattr(book, "colour_map", {}) or {}
    rgb = cmap.get(color_index)
    if not rgb:
        return None
    r, g, b = rgb
    return f"{r:02X}{g:02X}{b:02X}"


def _map_alignment(xf: Any) -> Alignment:
    al = xf.alignment
    hor = {
        0: None,
        1: "left",
        2: "center",
        3: "right",
        4: "fill",
        5: "justify",
        6: "centerContinuous",
        7: "distributed",
    }.get(al.hor_align)
    ver = {
        0: None,
        1: "top",
        2: "center",
        3: "bottom",
        4: "justify",
        5: "distributed",
    }.get(al.vert_align)
    return Alignment(horizontal=hor, vertical=ver, wrap_text=bool(al.text_wrapped))


def _map_font(book: Any, xf: Any) -> Font:
    f = book.font_list[xf.font_index]
    color = _xl_color_to_hex(book, getattr(f, "colour_index", None))
    return Font(
        name=f.name or "Calibri",
        size=(f.height or 200) / 20.0,  # xlrd: 高度单位是 1/20 pt
        bold=bool(f.bold),
        italic=bool(f.italic),
        underline="single" if getattr(f, "underlined", 0) else None,
        color=color if color else None,
        strike=bool(getattr(f, "struck_out", 0)),
    )


def _map_fill(book: Any, xf: Any) -> PatternFill | None:
    # 仅做最常见的纯色填充映射：pattern 1/solid（不同版本 BIFF 的编号可能差异）
    bg = xf.background
    # pattern_colour_index 通常用于纯色
    fg_hex = _xl_color_to_hex(book, getattr(bg, "pattern_colour_index", None))
    if fg_hex:
        return PatternFill(fill_type="solid", fgColor=fg_hex)
    return None


def _map_border(book: Any, xf: Any) -> Border:
    b = xf.border

    # line style: 0=none, 1=thin, 2=medium, ... 简化为 thin/medium/dashed/dotted 等
    def side(style: int, color_idx: int | None) -> Side:
        if style == 0:
            return Side(style=None)
        style_map: dict[int, BorderStyle] = {
            1: "thin",
            2: "medium",
            3: "dashed",
            4: "dotted",
            5: "thick",
            6: "double",
            7: "hair",
            8: "mediumDashed",
            9: "dashDot",
            10: "mediumDashDot",
            11: "dashDotDot",
            12: "mediumDashDotDot",
            13: "slantDashDot",
        }
        color = _xl_color_to_hex(book, color_idx)
        return Side(style=style_map.get(style, "thin"), color=color if color else None)

    return Border(
        left=side(b.left_line_style, b.left_colour_index),
        right=side(b.right_line_style, b.right_colour_index),
        top=side(b.top_line_style, b.top_colour_index),
        bottom=side(b.bottom_line_style, b.bottom_colour_index),
        diagonal=None,
    )


def _write_cell(ws: Worksheet, r: int, c: int, value: Any, ctype: int, book: Any, xf: int) -> None:
    """把 xlrd 的单元格写入 openpyxl，并尽量套用样式与格式"""
    cell = ws.cell(row=r + 1, column=c + 1)

    # 值与类型：日期需要转换
    if ctype == xlrd.XL_CELL_DATE:
        try:
            dt = xlrd.xldate_as_datetime(value, book.datemode)
            cell.value = dt
        except Exception:
            cell.value = value  # 失败就原样写
    elif ctype == xlrd.XL_CELL_BOOLEAN:
        cell.value = bool(value)
    elif ctype in (
        xlrd.XL_CELL_NUMBER,
        xlrd.XL_CELL_TEXT,
        xlrd.XL_CELL_EMPTY,
        xlrd.XL_CELL_ERROR,
        xlrd.XL_CELL_BLANK,
    ):
        cell.value = value
    else:
        cell.value = value

    # 数字格式
    fmt = None
    try:
        fmt_key = book.xf_list[xf].format_key
        fmt_obj = book.format_map.get(fmt_key)
        if fmt_obj:
            fmt = fmt_obj.format_str
    except Exception:
        pass
    if fmt:
        # openpyxl 能理解常见格式；少数 BIFF 占位符可能不兼容
        cell.number_format = fmt

    # 样式（字体、对齐、填充、边框）
    try:
        xfo = book.xf_list[xf]
        cell.font = _map_font(book, xfo)
        cell.alignment = _map_alignment(xfo)
        fill = _map_fill(book, xfo)
        if fill:
            cell.fill = fill
        cell.border = _map_border(book, xfo)
    except Exception:
        pass


def load_xls_as_openpyxl_preserve(path: str | Path | BinaryIO) -> Workbook:
    """
    尽量保留格式地把 .xls 转为 openpyxl.Workbook
    注意：公式会变为“已计算值”（xlrd 无法获取 .xls 公式文本）
    """
    # formatting_info=True 让 xlrd 暴露 XF 等格式信息
    if isinstance(path, (str, Path)):
        book = xlrd.open_workbook(str(path), formatting_info=True)
    else:
        position = path.tell()
        try:
            path.seek(0)
            book = xlrd.open_workbook(file_contents=path.read(), formatting_info=True)
        finally:
            path.seek(position)
    wb = Workbook()

    # 删除默认空 sheet
    wb.remove(wb.worksheets[0])

    for sidx in range(book.nsheets):
        sh = book.sheet_by_index(sidx)
        ws = wb.create_sheet(title=sh.name)

        # 写值 + 样式
        for r in range(sh.nrows):
            for c in range(sh.ncols):
                cell_obj = sh.cell(r, c)
                _write_cell(ws, r, c, cell_obj.value, cell_obj.ctype, book, cell_obj.xf_index)

        # 合并单元格
        # merged_cells: (rlo, rhi, clo, chi) 其中 rhi/chi 是“结束行/列的下一位”
        for rlo, rhi, clo, chi in getattr(sh, "merged_cells", []):
            ws.merge_cells(start_row=rlo + 1, start_column=clo + 1, end_row=rhi, end_column=chi)

        # 行高
        for r, ri in getattr(sh, "rowinfo_map", {}).items():
            # xlrd: height 单位 1/20 pt；0 表示默认
            if ri.height and ri.height > 0:
                ws.row_dimensions[r + 1].height = ri.height / 20.0

        # 列宽
        for c, ci in getattr(sh, "colinfo_map", {}).items():
            # xlrd: width 以 1/256 字符宽近似；openpyxl 直接用“字符宽”
            col_letter = get_column_letter(c + 1)
            ws.column_dimensions[col_letter].width = max(0.5, ci.width / 256.0)

    return wb


# 你已有的 .xls -> openpyxl.Workbook 转换函数
# def load_xls_as_openpyxl_preserve(file_path_or_bytes) -> Workbook: ...


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

            # OLE 型（传统 .xls）
            if cls._is_ole_xls(src):
                try:
                    wb = load_xls_as_openpyxl_preserve(src)
                    if not isinstance(wb, Workbook):
                        raise ExcelLoadError("转换后的对象不是 openpyxl.Workbook")
                    return wb
                except Exception as e:
                    logger.warning(f".xls 转换失败: {e}")
                    raise ExcelLoadError("无法读取 .xls（OLE）文件，建议另存为 .xlsx 后重试。") from e

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
        from ppap_core.settings import Settings

        from ppap_docops.files import download_file_to_temp_sync, parse_file_size

        settings = Settings()

        max_file_size = parse_file_size(settings.MAX_FILE_SIZE)
        set(settings.ALLOWED_EXTENSIONS)

        downloads_dir = Path("./downloads")
        downloads_dir.mkdir(parents=True, exist_ok=True)

        temp_download_path = download_file_to_temp_sync(file_path, downloads_dir, max_file_size)
        file_path = temp_download_path
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
