from pathlib import Path
from typing import Any

import openpyxl

# --- 1. 定义上下文与基类 ---

LayoutBox = dict[str, Any]
LayoutItem = LayoutBox | list[LayoutBox]


class ProcessingContext:
    def __init__(
        self,
        pdf_path: str | Path,
        raw_structures_data: dict[str, Any],
        final_layout_structure: list[LayoutItem],
        output_excel_path: str | Path,
        output_html_path: str | Path,
        img_w: int,
        img_h: int,
        padding: int,
    ) -> None:
        self.pdf_path = pdf_path
        self.raw_data = raw_structures_data  # 原始数据，用于ParagraphProcessor回溯表格坐标
        self.layout_structure = final_layout_structure  # 这是一个可变的列表，随处理链减少

        # 输出路径
        self.output_excel_path = output_excel_path
        self.output_html_path = output_html_path

        self.img_w = img_w
        self.img_h = img_h
        self.padding = padding

        # 共享状态
        self.workbook = openpyxl.Workbook()
        if "Sheet" in self.workbook.sheetnames:
            self.workbook.remove(self.workbook["Sheet"])
        self.excel_sheet_counter = 1

        # 待 Gemma 处理的图片列表 (存 PIL Image 对象或路径)
        self.images_for_gemma: list[dict[str, Any]] = []


class BaseProcessor:
    def process(self, context: ProcessingContext) -> None:
        """处理逻辑，如果 layout_structure 为空则应提前返回"""
        raise NotImplementedError
