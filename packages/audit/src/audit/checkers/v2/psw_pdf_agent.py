"""PSW PDF audit agent using shared structured/visual evidence selection."""

from audit.engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin
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
