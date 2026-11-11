"""PSW PDF audit agent using shared structured/visual evidence selection."""

from audit.contracts import AuditResultItem
from audit.engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin
from audit.engine.checkers.psw_visual_evidence import DATE_MISSING_NG
from audit.kinds import PpapDocumentType

from .pdf_excel_delegator_agent import PdfExcelDelegatorAgent
from .psw_excel_agent import PswExcelAgent


class PswPDFAgent(PswPdfEvidenceMixin, PdfExcelDelegatorAgent):
    """Audit PSW PDFs with deterministic fields and original-page visuals."""

    DOC_TYPE = PpapDocumentType.PSW
    TARGET_EXCEL_AGENT_CLASS = PswExcelAgent
    FALLBACK_AGENT_RULES = [
        "附件列内容检查",
        "重量字段检查",
        "印章检查",
        "供应商名称/供应商代码格式检查",
        "签字和盖章检查",
        "必填字段检查",
        "提交原因检查",
        "认可情况检查",
        "条件批准日期检查",
        "条件批准补充评论检查",
    ]

    def _run_fact_rule(self, ith: int, rule_no: int) -> AuditResultItem:
        """理想 rule 9 reads the original page like the Excel path: a blank or unrecognisable date cannot be judged."""
        result = super()._run_fact_rule(ith, rule_no)
        if rule_no == 9 and result.status == "NG" and result.suggestions == DATE_MISSING_NG:
            return result.model_copy(update={"status": "UNABLE", "suggestions": "条件批准日期为空或无法识别，无法判断；请核对原件。"})
        return result
