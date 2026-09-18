"""PSW PDF audit agent using shared structured/visual evidence selection."""

from ppap_excel_audit_engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin

from .pdf_excel_delegator_agent import PdfExcelDelegatorAgent
from .psw_excel_agent import PswExcelAgent


class PswPDFAgent(PswPdfEvidenceMixin, PdfExcelDelegatorAgent):
    """Audit PSW PDFs with deterministic fields and original-page visuals."""

    TARGET_EXCEL_AGENT_CLASS = PswExcelAgent
    FALLBACK_AGENT_RULES = [
        "生产件批准交付物文件清单附件检查",
        "重量字段格式检查",
        "印章及授权声明检查",
        "供应商名称格式检查",
        "签字与盖章完整性检查",
        "关键字段非空检查",
        "提交原因选项检查",
        "认可情况及结果勾选检查",
        "条件批准日期时效检查",
        "条件批准补充评论检查",
    ]
