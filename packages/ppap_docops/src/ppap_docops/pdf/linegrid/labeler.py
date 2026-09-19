"""PDF标注工具 - 在PDF页面上添加线条和点"""

import io
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class LineElement:
    """PDF线条元素类型定义"""

    def __init__(
        self,
        type: str,
        page: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        stroke_color: Optional[Tuple[float, float, float]] = None,
        fill_color: Optional[Tuple[float, float, float]] = None,
        stroke_opacity: Optional[float] = None,
        fill_opacity: Optional[float] = None,
    ):
        self.type = type
        self.page = page
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.stroke_color = stroke_color
        self.fill_color = fill_color
        self.stroke_opacity = stroke_opacity
        self.fill_opacity = fill_opacity


class LineType(Enum):
    """线条类型枚举"""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    DOT = "dot"


class PDFLabeler:
    """PDF标注器 - 用于在PDF页面上添加线条和点"""

    def __init__(self) -> None:
        self.color: tuple[float, float, float] = (1.0, 0.0, 0.0)  # 默认红色 (RGB)
        self.opacity = 1.0  # 默认不透明

    def set_color(self, color: Union[Tuple[float, float, float], str]) -> None:
        """设置标注颜色

        Args:
            color: RGB元组(0-1范围)或颜色名称字符串
        """
        if isinstance(color, str):
            color_map = {
                "red": (1, 0, 0),
                "green": (0, 1, 0),
                "blue": (0, 0, 1),
                "black": (0, 0, 0),
                "white": (1, 1, 1),
                "yellow": (1, 1, 0),
                "cyan": (0, 1, 1),
                "magenta": (1, 0, 1),
            }
            self.color = color_map.get(color.lower(), (1, 0, 0))
        else:
            self.color = color

    def set_opacity(self, opacity: float) -> None:
        """设置标注透明度

        Args:
            opacity: 透明度值，0-1范围
        """
        self.opacity = max(0.0, min(1.0, opacity))

    def add_horizontal_line(
        self,
        file_content: bytes,
        page_num: int,
        y: float,
        x_start: Optional[float] = None,
        x_end: Optional[float] = None,
        thickness: float = 1.0,
    ) -> bytes:
        """在PDF页面上添加水平线

        Args:
            file_content: PDF文件的二进制内容
            page_num: 页面编号（从0开始）
            y: 线条的Y坐标
            x_start: 线条起始X坐标（默认为页面左边）
            x_end: 线条结束X坐标（默认为页面右边）
            thickness: 线条粗细

        Returns:
            bytes: 修改后的PDF文件内容
        """
        try:
            import fitz

            pdf_doc = fitz.open(stream=file_content, filetype="pdf")

            if page_num >= len(pdf_doc):
                raise ValueError(f"页面编号 {page_num} 超出PDF页面范围 (0-{len(pdf_doc) - 1})")

            page = pdf_doc[page_num]
            page_rect = page.rect

            # 默认横跨整个页面宽度
            if x_start is None:
                x_start = page_rect.x0
            if x_end is None:
                x_end = page_rect.x1

            # 创建线条路径
            line_start = fitz.Point(x_start, y)
            line_end = fitz.Point(x_end, y)

            # 绘制线条
            shape = page.new_shape()
            shape.draw_line(line_start, line_end)
            shape.finish(width=thickness, color=self.color, stroke_opacity=self.opacity)
            shape.commit()

            # 保存修改后的PDF
            output_buffer = io.BytesIO()
            pdf_doc.save(output_buffer)
            pdf_doc.close()

            result = output_buffer.getvalue()
            output_buffer.close()

            logger.debug(f"成功在页面 {page_num} 添加水平线 (y={y}, x={x_start}-{x_end}, 粗细={thickness})")
            return result

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            logger.error(f"添加水平线失败: {e}")
            raise

    def add_vertical_line(
        self,
        file_content: bytes,
        page_num: int,
        x: float,
        y_start: Optional[float] = None,
        y_end: Optional[float] = None,
        thickness: float = 1.0,
    ) -> bytes:
        """在PDF页面上添加垂直线

        Args:
            file_content: PDF文件的二进制内容
            page_num: 页面编号（从0开始）
            x: 线条的X坐标
            y_start: 线条起始Y坐标（默认为页面顶部）
            y_end: 线条结束Y坐标（默认为页面底部）
            thickness: 线条粗细

        Returns:
            bytes: 修改后的PDF文件内容
        """
        try:
            import fitz

            pdf_doc = fitz.open(stream=file_content, filetype="pdf")

            if page_num >= len(pdf_doc):
                raise ValueError(f"页面编号 {page_num} 超出PDF页面范围 (0-{len(pdf_doc) - 1})")

            page = pdf_doc[page_num]
            page_rect = page.rect

            # 默认跨越整个页面高度
            if y_start is None:
                y_start = page_rect.y0
            if y_end is None:
                y_end = page_rect.y1

            # 创建线条路径
            line_start = fitz.Point(x, y_start)
            line_end = fitz.Point(x, y_end)

            # 绘制线条
            shape = page.new_shape()
            shape.draw_line(line_start, line_end)
            shape.finish(width=thickness, color=self.color, stroke_opacity=self.opacity)
            shape.commit()

            # 保存修改后的PDF
            output_buffer = io.BytesIO()
            pdf_doc.save(output_buffer)
            pdf_doc.close()

            result = output_buffer.getvalue()
            output_buffer.close()

            logger.debug(f"成功在页面 {page_num} 添加垂直线 (x={x}, y={y_start}-{y_end}, 粗细={thickness})")
            return result

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            logger.error(f"添加垂直线失败: {e}")
            raise

    def add_dot(self, file_content: bytes, page_num: int, x: float, y: float, size: float = 3.0) -> bytes:
        """在PDF页面上添加点

        Args:
            file_content: PDF文件的二进制内容
            page_num: 页面编号（从0开始）
            x: 点的X坐标
            y: 点的Y坐标
            size: 点的大小（半径）

        Returns:
            bytes: 修改后的PDF文件内容
        """
        try:
            import fitz

            pdf_doc = fitz.open(stream=file_content, filetype="pdf")

            if page_num >= len(pdf_doc):
                raise ValueError(f"页面编号 {page_num} 超出PDF页面范围 (0-{len(pdf_doc) - 1})")

            page = pdf_doc[page_num]

            # 创建圆形点
            center = fitz.Point(x, y)

            # 绘制圆点
            shape = page.new_shape()
            shape.draw_circle(center, size)
            shape.finish(
                width=0,  # 无边框
                color=self.color,
                fill=self.color,
                fill_opacity=self.opacity,
            )
            shape.commit()

            # 保存修改后的PDF
            output_buffer = io.BytesIO()
            pdf_doc.save(output_buffer)
            pdf_doc.close()

            result = output_buffer.getvalue()
            output_buffer.close()

            logger.debug(f"成功在页面 {page_num} 添加点 (x={x}, y={y}, 大小={size})")
            return result

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            logger.error(f"添加点失败: {e}")
            raise

    def add_multiple_marks(self, file_content: bytes, marks: List[dict[str, Any]]) -> bytes:
        """在PDF上添加多个标记

        Args:
            file_content: PDF文件的二进制内容
            marks: 标记列表，每个标记是包含以下键的字典：
                - type: 'horizontal', 'vertical', 或 'dot'
                - page: 页面编号
                - x: X坐标（对于垂直线和点）
                - y: Y坐标（对于水平线和点）
                - x_start, x_end: 水平线的起止X坐标（可选）
                - y_start, y_end: 垂直线的起止Y坐标（可选）
                - thickness: 线条粗细（可选，默认1.0）
                - size: 点的大小（可选，默认3.0）
                - color: 颜色（可选）
                - opacity: 透明度（可选）

        Returns:
            bytes: 修改后的PDF文件内容
        """
        try:
            import fitz

            pdf_doc = fitz.open(stream=file_content, filetype="pdf")

            # 按页面分组标记
            marks_by_page: dict[int, list[dict[str, Any]]] = {}
            for mark in marks:
                page_num = int(mark.get("page", 0))
                if page_num not in marks_by_page:
                    marks_by_page[page_num] = []
                marks_by_page[page_num].append(mark)

            # 处理每个页面的标记
            for page_num, page_marks in marks_by_page.items():
                if page_num >= len(pdf_doc):
                    logger.warning(f"跳过页面 {page_num}，超出PDF页面范围")
                    continue

                page = pdf_doc[page_num]
                page_rect = page.rect
                shape = page.new_shape()

                for mark in page_marks:
                    mark_type = mark.get("type", "").lower()

                    # 设置标记特定的颜色和透明度
                    mark_color = mark.get("color", self.color)
                    mark_opacity = mark.get("opacity", self.opacity)

                    if isinstance(mark_color, str):
                        color_map = {
                            "red": (1, 0, 0),
                            "green": (0, 1, 0),
                            "blue": (0, 0, 1),
                            "black": (0, 0, 0),
                            "white": (1, 1, 1),
                            "yellow": (1, 1, 0),
                            "cyan": (0, 1, 1),
                            "magenta": (1, 0, 1),
                        }
                        mark_color = color_map.get(mark_color.lower(), (1, 0, 0))

                    if mark_type == "horizontal":
                        y = mark.get("y", page_rect.height / 2)
                        x_start = mark.get("x_start", page_rect.x0)
                        x_end = mark.get("x_end", page_rect.x1)
                        thickness = mark.get("thickness", 1.0)

                        line_start = fitz.Point(x_start, y)
                        line_end = fitz.Point(x_end, y)
                        shape.draw_line(line_start, line_end)
                        shape.finish(
                            width=thickness,
                            color=mark_color,
                            stroke_opacity=mark_opacity,
                        )

                    elif mark_type == "vertical":
                        x = mark.get("x", page_rect.width / 2)
                        y_start = mark.get("y_start", page_rect.y0)
                        y_end = mark.get("y_end", page_rect.y1)
                        thickness = mark.get("thickness", 1.0)

                        line_start = fitz.Point(x, y_start)
                        line_end = fitz.Point(x, y_end)
                        shape.draw_line(line_start, line_end)
                        shape.finish(
                            width=thickness,
                            color=mark_color,
                            stroke_opacity=mark_opacity,
                        )

                    elif mark_type == "dot":
                        x = mark.get("x", page_rect.width / 2)
                        y = mark.get("y", page_rect.height / 2)
                        size = mark.get("size", 3.0)

                        center = fitz.Point(x, y)
                        shape.draw_circle(center, size)
                        shape.finish(
                            width=0,
                            color=mark_color,
                            fill=mark_color,
                            fill_opacity=mark_opacity,
                        )

                    else:
                        logger.warning(f"未知的标记类型: {mark_type}")

                shape.commit()

            # 保存修改后的PDF
            output_buffer = io.BytesIO()
            pdf_doc.save(output_buffer)
            pdf_doc.close()

            result = output_buffer.getvalue()
            output_buffer.close()

            logger.debug(f"成功添加 {len(marks)} 个标记到PDF")
            return result

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            logger.error(f"添加多个标记失败: {e}")
            raise

    def extract_lines_and_rects(
        self,
        file_content: bytes,
        handle_rotation: bool = True,
        color_filter: Optional[Dict[str, Any]] = None,
        selected_pages: Optional[Iterable[int]] = None,
    ) -> List[LineElement]:
        """提取PDF中的所有线条和矩形，并处理页面旋转

        Args:
            file_content: PDF文件的二进制内容
            handle_rotation: 是否处理页面旋转
            color_filter: 颜色过滤条件，支持以下选项：
                - stroke_colors: List[Tuple[float, float, float]] - 要包含的描边颜色列表
                - fill_colors: List[Tuple[float, float, float]] - 要包含的填充颜色列表
                - exclude_stroke_colors: List[Tuple[float, float, float]] - 要排除的描边颜色列表
                - exclude_fill_colors: List[Tuple[float, float, float]] - 要排除的填充颜色列表
                - color_tolerance: float - 颜色匹配容差 (0-1, 默认0.01)
                - require_stroke: bool - 是否只返回有描边的元素
                - require_fill: bool - 是否只返回有填充的元素
            selected_pages: 可选的 0-based 页码集合；为 None 时提取全部页面

        Returns:
            List[LineElement]: 包含线条和矩形信息的列表，坐标已根据页面旋转调整
        """
        try:
            import fitz

            pdf_doc = fitz.open(stream=file_content, filetype="pdf")
            elements: List[LineElement] = []

            # 解析颜色过滤参数
            if color_filter is None:
                color_filter = {}

            stroke_colors = color_filter.get("stroke_colors", [])
            fill_colors = color_filter.get("fill_colors", [])
            exclude_stroke_colors = color_filter.get("exclude_stroke_colors", [])
            exclude_fill_colors = color_filter.get("exclude_fill_colors", [])
            color_tolerance = color_filter.get("color_tolerance", 0.01)
            require_stroke = color_filter.get("require_stroke", False)
            require_fill = color_filter.get("require_fill", False)

            page_numbers = self._resolve_selected_page_numbers(len(pdf_doc), selected_pages)
            for page_num in page_numbers:
                page = pdf_doc[page_num]

                # 获取页面旋转信息
                page_rotation = page.rotation if handle_rotation else 0
                page_rect = page.rect
                media_box = page.mediabox
                crop_box = page.cropbox

                drawings = page.get_drawings()

                for drawing in drawings:
                    # 提取绘图级别的颜色信息
                    drawing_stroke_color = drawing.get("color")
                    drawing_fill_color = drawing.get("fill")
                    drawing_stroke_opacity = drawing.get("stroke_opacity")
                    drawing_fill_opacity = drawing.get("fill_opacity")

                    # 应用颜色过滤
                    if not self._passes_color_filter(
                        drawing_stroke_color,
                        drawing_fill_color,
                        drawing_stroke_opacity,
                        drawing_fill_opacity,
                        stroke_colors,
                        fill_colors,
                        exclude_stroke_colors,
                        exclude_fill_colors,
                        color_tolerance,
                        require_stroke,
                        require_fill,
                    ):
                        continue

                    for item in drawing.get("items", []):
                        if isinstance(item, (list, tuple)) and item:
                            item_type = item[0] if item else ""

                            if item_type == "l" and len(item) >= 3:  # line: ["l", point1, point2]
                                start_point = item[1]
                                end_point = item[2]

                                # 应用坐标变换
                                if handle_rotation and page_rotation != 0:
                                    start_bbox = [
                                        start_point.x,
                                        start_point.y,
                                        start_point.x,
                                        start_point.y,
                                    ]
                                    end_bbox = [
                                        end_point.x,
                                        end_point.y,
                                        end_point.x,
                                        end_point.y,
                                    ]

                                    start_transformed = self._transform_coordinates_for_rotation(
                                        start_bbox,
                                        page_rotation,
                                        page_rect,
                                        media_box,
                                        crop_box,
                                    )
                                    end_transformed = self._transform_coordinates_for_rotation(
                                        end_bbox,
                                        page_rotation,
                                        page_rect,
                                        media_box,
                                        crop_box,
                                    )

                                    elements.append(
                                        LineElement(
                                            type="line",
                                            page=page_num,
                                            x0=start_transformed[0],
                                            y0=start_transformed[1],
                                            x1=end_transformed[0],
                                            y1=end_transformed[1],
                                            stroke_color=drawing_stroke_color,
                                            fill_color=drawing_fill_color,
                                            stroke_opacity=drawing_stroke_opacity,
                                            fill_opacity=drawing_fill_opacity,
                                        )
                                    )
                                else:
                                    elements.append(
                                        LineElement(
                                            type="line",
                                            page=page_num,
                                            x0=start_point.x,
                                            y0=start_point.y,
                                            x1=end_point.x,
                                            y1=end_point.y,
                                            stroke_color=drawing_stroke_color,
                                            fill_color=drawing_fill_color,
                                            stroke_opacity=drawing_stroke_opacity,
                                            fill_opacity=drawing_fill_opacity,
                                        )
                                    )

                            elif item_type == "re" and len(item) >= 2:  # rectangle: ["re", rect]
                                rect = item[1]
                                # 获取矩形四个角的坐标
                                corners = [
                                    [rect.x0, rect.y0],  # 左下角
                                    [rect.x1, rect.y0],  # 右下角
                                    [rect.x1, rect.y1],  # 右上角
                                    [rect.x0, rect.y1],  # 左上角
                                ]

                                # 应用坐标变换
                                if handle_rotation and page_rotation != 0:
                                    transformed_corners = []
                                    for corner in corners:
                                        corner_bbox = [
                                            corner[0],
                                            corner[1],
                                            corner[0],
                                            corner[1],
                                        ]
                                        transformed = self._transform_coordinates_for_rotation(
                                            corner_bbox,
                                            page_rotation,
                                            page_rect,
                                            media_box,
                                            crop_box,
                                        )
                                        transformed_corners.append([transformed[0], transformed[1]])

                                    # 创建变换后的矩形边
                                    for i in range(4):
                                        next_i = (i + 1) % 4
                                        elements.append(
                                            LineElement(
                                                type="line",
                                                page=page_num,
                                                x0=transformed_corners[i][0],
                                                y0=transformed_corners[i][1],
                                                x1=transformed_corners[next_i][0],
                                                y1=transformed_corners[next_i][1],
                                                stroke_color=drawing_stroke_color,
                                                fill_color=drawing_fill_color,
                                                stroke_opacity=drawing_stroke_opacity,
                                                fill_opacity=drawing_fill_opacity,
                                            )
                                        )
                                else:
                                    # 矩形用四条线来表示
                                    # 上边
                                    elements.append(
                                        LineElement(
                                            type="line",
                                            page=page_num,
                                            x0=rect.x0,
                                            y0=rect.y0,
                                            x1=rect.x1,
                                            y1=rect.y0,
                                            stroke_color=drawing_stroke_color,
                                            fill_color=drawing_fill_color,
                                            stroke_opacity=drawing_stroke_opacity,
                                            fill_opacity=drawing_fill_opacity,
                                        )
                                    )
                                    # 右边
                                    elements.append(
                                        LineElement(
                                            type="line",
                                            page=page_num,
                                            x0=rect.x1,
                                            y0=rect.y0,
                                            x1=rect.x1,
                                            y1=rect.y1,
                                            stroke_color=drawing_stroke_color,
                                            fill_color=drawing_fill_color,
                                            stroke_opacity=drawing_stroke_opacity,
                                            fill_opacity=drawing_fill_opacity,
                                        )
                                    )
                                    # 下边
                                    elements.append(
                                        LineElement(
                                            type="line",
                                            page=page_num,
                                            x0=rect.x1,
                                            y0=rect.y1,
                                            x1=rect.x0,
                                            y1=rect.y1,
                                            stroke_color=drawing_stroke_color,
                                            fill_color=drawing_fill_color,
                                            stroke_opacity=drawing_stroke_opacity,
                                            fill_opacity=drawing_fill_opacity,
                                        )
                                    )
                                    # 左边
                                    elements.append(
                                        LineElement(
                                            type="line",
                                            page=page_num,
                                            x0=rect.x0,
                                            y0=rect.y1,
                                            x1=rect.x0,
                                            y1=rect.y0,
                                            stroke_color=drawing_stroke_color,
                                            fill_color=drawing_fill_color,
                                            stroke_opacity=drawing_stroke_opacity,
                                            fill_opacity=drawing_fill_opacity,
                                        )
                                    )

            pdf_doc.close()
            logger.debug(f"提取到 {len(elements)} 个线条和矩形元素")
            return elements

        except Exception as e:
            logger.error(f"提取线条和矩形失败: {e}")
            raise

    def _resolve_selected_page_numbers(
        self,
        page_count: int,
        selected_pages: Optional[Iterable[int]],
    ) -> Iterable[int]:
        if selected_pages is None:
            return range(page_count)

        normalized = sorted({page_num for page_num in (int(page) for page in selected_pages) if 0 <= page_num < page_count})
        return normalized

    def _passes_color_filter(
        self,
        stroke_color: Optional[Tuple[float, float, float]],
        fill_color: Optional[Tuple[float, float, float]],
        stroke_opacity: Optional[float],
        fill_opacity: Optional[float],
        stroke_colors: List[Tuple[float, float, float]],
        fill_colors: List[Tuple[float, float, float]],
        exclude_stroke_colors: List[Tuple[float, float, float]],
        exclude_fill_colors: List[Tuple[float, float, float]],
        color_tolerance: float,
        require_stroke: bool,
        require_fill: bool,
    ) -> bool:
        """检查绘图元素是否通过颜色过滤条件

        Args:
            stroke_color: 描边颜色
            fill_color: 填充颜色
            stroke_opacity: 描边透明度
            fill_opacity: 填充透明度
            stroke_colors: 要包含的描边颜色列表
            fill_colors: 要包含的填充颜色列表
            exclude_stroke_colors: 要排除的描边颜色列表
            exclude_fill_colors: 要排除的填充颜色列表
            color_tolerance: 颜色匹配容差
            require_stroke: 是否要求有描边
            require_fill: 是否要求有填充

        Returns:
            bool: 是否通过过滤条件
        """
        # 检查是否要求有描边/填充
        if require_stroke and (stroke_color is None or stroke_opacity is None or stroke_opacity == 0):
            return False

        if require_fill and (fill_color is None or fill_opacity is None or fill_opacity == 0):
            return False

        # 检查排除的颜色
        if stroke_color and exclude_stroke_colors:
            for exclude_color in exclude_stroke_colors:
                if self._colors_match(stroke_color, exclude_color, color_tolerance):
                    return False

        if fill_color and exclude_fill_colors:
            for exclude_color in exclude_fill_colors:
                if self._colors_match(fill_color, exclude_color, color_tolerance):
                    return False

        # 检查包含的颜色（如果指定了）
        if stroke_colors:
            if stroke_color is None:
                return False
            stroke_match = any(self._colors_match(stroke_color, target_color, color_tolerance) for target_color in stroke_colors)
            if not stroke_match:
                return False

        if fill_colors:
            if fill_color is None:
                return False
            fill_match = any(self._colors_match(fill_color, target_color, color_tolerance) for target_color in fill_colors)
            if not fill_match:
                return False

        return True

    def _colors_match(
        self,
        color1: Tuple[float, float, float],
        color2: Tuple[float, float, float],
        tolerance: float,
    ) -> bool:
        """检查两个颜色是否在容差范围内匹配

        Args:
            color1: 第一个颜色 (r, g, b)
            color2: 第二个颜色 (r, g, b)
            tolerance: 容差值

        Returns:
            bool: 是否匹配
        """
        return all(abs(c1 - c2) <= tolerance for c1, c2 in zip(color1, color2))

    def _transform_coordinates_for_rotation(
        self,
        bbox: List[float],
        rotation: int,
        page_rect: Any,
        media_box: Any,
        crop_box: Any,
    ) -> List[float]:
        """根据页面旋转角度变换坐标系统

        Args:
            bbox: 原始边界框 [x0, y0, x1, y1]
            rotation: 页面旋转角度 (0, 90, 180, 270)
            page_rect: 页面矩形
            media_box: 媒体框
            crop_box: 裁剪框

        Returns:
            List[float]: 变换后的边界框 [x0, y0, x1, y1]
        """
        try:
            x0, y0, x1, y1 = bbox

            # 如果没有旋转，直接返回原坐标
            if rotation == 0:
                return [x0, y0, x1, y1]

            # 获取页面尺寸
            page_width = page_rect.width
            page_height = page_rect.height

            # 根据旋转角度应用坐标变换
            if rotation == 90:
                # 顺时针旋转90度: (x, y) -> (page_height - y, x)
                new_x0 = page_height - y1
                new_y0 = x0
                new_x1 = page_height - y0
                new_y1 = x1
            elif rotation == 180:
                # 旋转180度: (x, y) -> (page_width - x, page_height - y)
                new_x0 = page_width - x1
                new_y0 = page_height - y1
                new_x1 = page_width - x0
                new_y1 = page_height - y0
            elif rotation == 270:
                # 逆时针旋转90度(顺时针270度): (x, y) -> (y, page_width - x)
                new_x0 = y0
                new_y0 = page_width - x1
                new_x1 = y1
                new_y1 = page_width - x0
            else:
                # 未知旋转角度，记录警告并返回原坐标
                logger.warning(f"未知的页面旋转角度: {rotation}°")
                return [x0, y0, x1, y1]

            # 确保坐标顺序正确 (x0 <= x1, y0 <= y1)
            final_x0 = min(new_x0, new_x1)
            final_x1 = max(new_x0, new_x1)
            final_y0 = min(new_y0, new_y1)
            final_y1 = max(new_y0, new_y1)

            return [final_x0, final_y0, final_x1, final_y1]

        except Exception as e:
            logger.error(f"坐标变换失败: {e}")
            return bbox  # 变换失败时返回原坐标

    def draw_line_elements(
        self,
        file_content: bytes,
        elements: List[LineElement] = [],
        thickness: float = 1.0,
        color: Union[Tuple[float, float, float], str] = "red",
        opacity: float = 0.5,
    ) -> bytes:
        """重新绘制PDF中的所有线条和矩形

        Args:
            file_content: PDF文件的二进制内容
            thickness: 线条粗细
            color: 线条颜色
            opacity: 透明度

        Returns:
            bytes: 重新绘制后的PDF文件内容
        """
        try:
            import fitz

            # 设置绘制参数
            original_color = self.color
            original_opacity = self.opacity

            self.set_color(color)
            self.set_opacity(opacity)

            pdf_doc = fitz.open(stream=file_content, filetype="pdf")

            # 按页面分组元素
            elements_by_page: Dict[int, List[LineElement]] = {}
            for element in elements:
                page_num = element.page
                if page_num not in elements_by_page:
                    elements_by_page[page_num] = []
                elements_by_page[page_num].append(element)

            # 在每个页面上重新绘制
            for page_num, page_elements in elements_by_page.items():
                if page_num >= len(pdf_doc):
                    continue

                page = pdf_doc[page_num]
                shape = page.new_shape()

                for element in page_elements:
                    if element.type == "line":
                        start_point = fitz.Point(element.x0, element.y0)
                        end_point = fitz.Point(element.x1, element.y1)

                        # 绘制线条
                        shape.draw_line(start_point, end_point)
                        shape.finish(
                            width=thickness,
                            color=self.color,
                            stroke_opacity=self.opacity,
                        )

                        # 在端点添加蓝色圆点
                        blue_color = (0, 0, 1)  # 蓝色
                        dot_radius = thickness / 2  # 点的半径等于线条粗细的一半

                        # 起点
                        shape.draw_circle(start_point, dot_radius)
                        shape.finish(
                            width=0,  # 无边框
                            color=blue_color,
                            fill=blue_color,
                            fill_opacity=self.opacity,
                        )

                        # 终点
                        shape.draw_circle(end_point, dot_radius)
                        shape.finish(
                            width=0,  # 无边框
                            color=blue_color,
                            fill=blue_color,
                            fill_opacity=self.opacity,
                        )

                shape.commit()

            # 保存修改后的PDF
            output_buffer = io.BytesIO()
            pdf_doc.save(output_buffer)
            pdf_doc.close()

            result = output_buffer.getvalue()
            output_buffer.close()

            # 恢复原始设置
            self.color = original_color
            self.opacity = original_opacity

            logger.debug(f"成功重新绘制 {len(elements)} 个线条和矩形元素")
            return result

        except ImportError:
            raise ImportError("PyMuPDF (fitz) 库未安装")
        except Exception as e:
            logger.error(f"重新绘制线条和矩形失败: {e}")
            raise

    def save_labeled_pdf(self, file_content: bytes, output_path: Union[str, Path]) -> None:
        """保存标注后的PDF到文件

        Args:
            file_content: 标注后的PDF文件内容
            output_path: 输出文件路径
        """
        try:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "wb") as f:
                f.write(file_content)

            logger.debug(f"标注后的PDF已保存到: {output_path}")

        except Exception as e:
            logger.error(f"保存PDF文件失败: {e}")
            raise


def add_horizontal_line_to_pdf(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    page_num: int,
    y: float,
    x_start: Optional[float] = None,
    x_end: Optional[float] = None,
    thickness: float = 1.0,
    color: Union[Tuple[float, float, float], str] = "red",
) -> None:
    """便捷函数：为PDF添加水平线

    Args:
        input_path: 输入PDF文件路径
        output_path: 输出PDF文件路径
        page_num: 页面编号（从0开始）
        y: 线条的Y坐标
        x_start: 线条起始X坐标（默认为页面左边）
        x_end: 线条结束X坐标（默认为页面右边）
        thickness: 线条粗细
        color: 线条颜色
    """
    labeler = PDFLabeler()
    labeler.set_color(color)

    with open(input_path, "rb") as f:
        content = f.read()

    result = labeler.add_horizontal_line(content, page_num, y, x_start, x_end, thickness)
    labeler.save_labeled_pdf(result, output_path)


def add_vertical_line_to_pdf(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    page_num: int,
    x: float,
    y_start: Optional[float] = None,
    y_end: Optional[float] = None,
    thickness: float = 1.0,
    color: Union[Tuple[float, float, float], str] = "red",
) -> None:
    """便捷函数：为PDF添加垂直线

    Args:
        input_path: 输入PDF文件路径
        output_path: 输出PDF文件路径
        page_num: 页面编号（从0开始）
        x: 线条的X坐标
        y_start: 线条起始Y坐标（默认为页面顶部）
        y_end: 线条结束Y坐标（默认为页面底部）
        thickness: 线条粗细
        color: 线条颜色
    """
    labeler = PDFLabeler()
    labeler.set_color(color)

    with open(input_path, "rb") as f:
        content = f.read()

    result = labeler.add_vertical_line(content, page_num, x, y_start, y_end, thickness)
    labeler.save_labeled_pdf(result, output_path)


def add_dot_to_pdf(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    page_num: int,
    x: float,
    y: float,
    size: float = 3.0,
    color: Union[Tuple[float, float, float], str] = "red",
) -> None:
    """便捷函数：为PDF添加点

    Args:
        input_path: 输入PDF文件路径
        output_path: 输出PDF文件路径
        page_num: 页面编号（从0开始）
        x: 点的X坐标
        y: 点的Y坐标
        size: 点的大小（半径）
        color: 点的颜色
    """
    labeler = PDFLabeler()
    labeler.set_color(color)

    with open(input_path, "rb") as f:
        content = f.read()

    result = labeler.add_dot(content, page_num, x, y, size)
    labeler.save_labeled_pdf(result, output_path)


# For usage examples, see: docs/examples/extractors/pdf_label_example.py
