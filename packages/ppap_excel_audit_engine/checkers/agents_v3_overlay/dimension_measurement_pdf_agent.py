"""
尺寸检验报告 PDF 审核代理（Delegator 版本）。
"""

import json
import logging
import os
import tempfile
from typing import Any, Optional

from ppap_core.contracts import AuditResultItem as AuditResult

from .dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from .pdf_excel_delegator_agent import PdfExcelDelegatorAgent

logger = logging.getLogger(__name__)


class DimensionMeasurementPDFAgent(PdfExcelDelegatorAgent):
    """尺寸检验报告专业审核代理"""

    _FALLBACK_IMPORTANCE_RULE_INDEX = -1

    TARGET_EXCEL_AGENT_CLASS = DimensionMeasurementExcelAgent
    FALLBACK_AGENT_RULES = list(DimensionMeasurementExcelAgent.EXPECTED_RULE_CATEGORIES)

    def preprocess_excel_path(self, path: str) -> str:
        return self._preprocess_ocr_excel(path)

    def fallback_rule_checkers(self, ith: int) -> Optional[AuditResult]:
        # 转换失败时仅规则2使用专有图像检查，其余规则回退统一错误结果。
        if ith == self._FALLBACK_IMPORTANCE_RULE_INDEX:
            return self.rule_1_importance_classification(ith)
        return None

    # ------------------------------------------------------------------
    # OCR 输出预处理
    # ------------------------------------------------------------------
    def _preprocess_ocr_excel(self, excel_path: str) -> str:
        """
        预处理 OCR 输出的多 sheet Excel。

        OCR 适配器会将 PDF 中每个表格区域拆为独立 sheet (Table_1, Table_2, ...),
        导致 ExcelAgent 无法在单一 sheet 上找到所需的结构标记
        （标题、重要度、列头、非尺寸/尺寸分区、表尾字段）。

        本方法将多 sheet 重建为 ExcelAgent 期望的单 sheet 结构。
        """
        import openpyxl

        wb = openpyxl.load_workbook(excel_path)

        # 单 sheet 无需预处理
        if len(wb.sheetnames) <= 1:
            wb.close()
            return excel_path

        logger.debug("OCR输出包含 %d 个sheet，开始预处理合并", len(wb.sheetnames))

        # ---- 第一步：按内容特征分类各 sheet ----
        non_dim_sheet = None
        importance_sheet = None
        footer_sheet = None
        unclassified = []

        for name in wb.sheetnames:
            ws = wb[name]
            classified = False

            for r in range(1, min(10, ws.max_row + 1)):
                if classified:
                    break
                for c in range(1, min(15, ws.max_column + 1)):
                    v = str(ws.cell(r, c).value or "").strip()
                    if "非尺寸" in v and non_dim_sheet is None:
                        non_dim_sheet = ws
                        classified = True
                        break
                    if "重要度区分" in v and importance_sheet is None:
                        importance_sheet = ws
                        classified = True
                        break
                    if ("车型" in v or "零件名称" in v) and footer_sheet is None:
                        footer_sheet = ws
                        classified = True
                        break

            if not classified:
                unclassified.append(ws)

        # 未分类中行数最多的视为尺寸数据 sheet
        dim_sheet = None
        if unclassified:
            dim_sheet = max(unclassified, key=lambda s: s.max_row)
            unclassified.remove(dim_sheet)

        logger.debug(
            "Sheet分类: 非尺寸=%s, 尺寸=%s, 重要度=%s, 表尾=%s, 其他=%s",
            non_dim_sheet and non_dim_sheet.title,
            dim_sheet and dim_sheet.title,
            importance_sheet and importance_sheet.title,
            footer_sheet and footer_sheet.title,
            [s.title for s in unclassified],
        )

        # ---- 第二步：构建新的单 sheet 工作簿 ----
        new_wb = openpyxl.Workbook()
        new_ws = new_wb.active
        if new_ws is None:
            new_ws = new_wb.create_sheet()
        new_ws.title = "表单"
        cur = 1

        # (1) 标题行
        new_ws.cell(cur, 1, "全功能检验报告")
        cur += 1

        # (2) 重要度区分
        if importance_sheet:
            for r in range(1, importance_sheet.max_row + 1):
                for c in range(1, importance_sheet.max_column + 1):
                    importance_value = importance_sheet.cell(r, c).value
                    if importance_value is not None:
                        new_ws.cell(cur, c, importance_value)
                cur += 1
        else:
            cur += 1

        # (3) 列头行
        col_headers = {
            1: "序号",
            2: "检查项目",
            3: "判定标准",
            4: "供方检查方法/检查工具",
            5: "检查数据",
            10: "判定结果",
        }
        for col_idx, text in col_headers.items():
            new_ws.cell(cur, col_idx, text)
        new_ws.merge_cells(start_row=cur, start_column=5, end_row=cur, end_column=9)
        cur += 1

        # 辅助函数：检测 OCR 重复的列头行（PDF 分页时表头会重复出现）
        header_markers = ["供方检查方法", "检查数据", "判定结果"]

        def is_repeated_header(sheet: Any, row_idx: int) -> bool:
            hits = 0
            for c in range(1, min(sheet.max_column + 1, 15)):
                v = str(sheet.cell(row_idx, c).value or "").replace(" ", "").replace("\n", "")
                for marker in header_markers:
                    if marker in v:
                        hits += 1
                        break
                if hits >= 2:
                    return True
            return False

        def append_section_rows(
            section_sheet: Any,
            section_label: str,
            *,
            start_row: int = 1,
        ) -> None:
            nonlocal cur
            new_ws.cell(cur, 1, section_label)
            cur += 1

            max_col = min(section_sheet.max_column, 10)
            for r in range(start_row, section_sheet.max_row + 1):
                if is_repeated_header(section_sheet, r):
                    logger.debug("跳过%s区域重复列头行: %s R%s", section_label, section_sheet.title, r)
                    continue
                has_data = False
                for c in range(1, max_col + 1):
                    v = section_sheet.cell(r, c).value
                    if v is not None:
                        new_ws.cell(cur, c, v)
                    has_data = True
                if has_data:
                    cur += 1

        # (4) "非尺寸" 分区
        if non_dim_sheet:
            start_r = 2 if "非尺寸" in str(non_dim_sheet.cell(1, 1).value or "").strip() else 1
            append_section_rows(non_dim_sheet, "非尺寸", start_row=start_r)

        # (5) "尺寸" 分区
        if dim_sheet:
            append_section_rows(dim_sheet, "尺寸")

        # (6) 表尾元数据
        footer_keywords = [
            "车型",
            "零件名称",
            "零件号",
            "供应商",
            "责任人",
            "日期",
            "备注",
        ]
        if footer_sheet:
            for r in range(1, footer_sheet.max_row + 1):
                row_text = "".join(str(footer_sheet.cell(r, c).value or "").replace(" ", "") for c in range(1, footer_sheet.max_column + 1))
                if any(kw in row_text for kw in footer_keywords):
                    for c in range(1, footer_sheet.max_column + 1):
                        footer_value = footer_sheet.cell(r, c).value
                        if footer_value is not None:
                            new_ws.cell(cur, c, footer_value)
                    cur += 1

        # ---- 第三步：保存新文件 ----
        fd, new_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        new_wb.save(new_path)
        new_wb.close()
        wb.close()

        logger.debug(
            "OCR Excel预处理完成: %s (%s行×%s列)",
            new_path,
            new_ws.max_row,
            new_ws.max_column,
        )
        return new_path

    def rule_1_importance_classification(self, ith: int) -> AuditResult:
        """
        规则2（index=1）：重要度区分标记检查。
        """
        logger.debug("开始执行规则2：重要度区分检查，索引：%s", ith)
        rule_data = self.get_rule_by_index(ith)
        if not rule_data:
            return self.create_error_result("无法获取规则数据", ith)

        if not self.images:
            self.images = self._get_pdf_images_sync()

        if not self.images:
            return self.create_error_result("无法将PDF渲染成图像用于分析。", ith)

        system_prompt = "你是一个专业的图片审核员，擅长识别文档中的重要度标记。"
        user_prompt = """
        请检查文档中是否标记了重要区分度：
        1. 查找文档中是否有"关键"、"重要"、"一般"等重要度标记
        2. 检查这些标记是否清晰可见
        3. 确认重要度区分是否按照标准进行标记

        请根据检查结果，严格按照以下JSON格式返回，不要添加任何额外解释：
        {
            "classification": "基本信息",
            "item": "重要度区分标记",
            "priority": "P0",
            "status": "PASS/NG/ERR",
            "suggestions": "如果status为NG，请说明具体的问题（未找到重要度区分图像）。如果为PASS，此项可为空。"
        }
        """

        response = self._analyze_images_with_prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            images=self.images,
            json_format=True,
        )
        logger.debug("规则2分析结果：%s", response)

        try:
            result_dict = json.loads(response.replace("```json", "").replace("```", "").strip())
            return AuditResult(**result_dict)
        except json.JSONDecodeError as exc:
            logger.error("解析AI响应失败: %s", exc)
            return AuditResult(
                classification="基本信息",
                item="重要度区分标记",
                priority="P0",
                status="NG",
                suggestions="未找到重要度区分图像",
            )
