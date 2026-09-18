"""
尺寸检验报告 PDF 审核代理（Delegator 版本）。
"""

import json
import logging
from typing import Optional

from ppap_core.contracts import AuditResultItem as AuditResult
from ppap_core.types import PpapDocumentType

from .dimension_measurement_excel_agent import DimensionMeasurementExcelAgent
from .pdf_excel_delegator_agent import PdfExcelDelegatorAgent

logger = logging.getLogger(__name__)


class DimensionMeasurementPDFAgent(PdfExcelDelegatorAgent):
    """尺寸检验报告专业审核代理"""

    _FALLBACK_IMPORTANCE_RULE_INDEX = 1

    DOC_TYPE = PpapDocumentType.DIMENSION_MEASUREMENT_REPORT
    TARGET_EXCEL_AGENT_CLASS = DimensionMeasurementExcelAgent
    FALLBACK_AGENT_RULES = [
        "标题识别检查",
        "重要度标记检查",
        "数值公差带检查",
        "检查方法/工具列检查",
        "数据唯一性检查",
        "非尺寸数据检查",
        "检查方法与数据匹配检查",
        "关键字段识别检查",
        "Centering阈值检查",
        "Range计算校验",
        "Range阈值检查",
    ]

    def fallback_rule_checkers(self, ith: int) -> Optional[AuditResult]:
        # 转换失败时仅规则2使用专有图像检查，其余规则回退统一错误结果。
        if ith == self._FALLBACK_IMPORTANCE_RULE_INDEX:
            return self.rule_1_importance_classification(ith)
        return None

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
