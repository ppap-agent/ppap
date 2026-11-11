"""
尺寸检验报告 PDF 审核代理（Delegator 版本）。
"""

import logging
from typing import Optional

from audit.contracts import AuditResultItem as AuditResult
from audit.kinds import PpapDocumentType

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
        from audit.engine.checkers.document_visual_checks import importance_visual_check

        return importance_visual_check(self, ith)
