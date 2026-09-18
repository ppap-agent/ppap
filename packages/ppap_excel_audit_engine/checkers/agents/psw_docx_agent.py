"""PSW DOCX audit using structured Word grids and embedded visual evidence."""

from ppap_excel_audit_engine.checkers.docx_workbook_agent import StructuredDocxWorkbookMixin
from ppap_excel_audit_engine.checkers.psw_docx_evidence import PswDocxEvidenceMixin

from .psw_excel_agent import PswExcelAgent


class PswDocxAgent(PswDocxEvidenceMixin, StructuredDocxWorkbookMixin, PswExcelAgent):
    """Reuse strict V2 PSW rules over authoritative Word evidence."""


__all__ = ["PswDocxAgent"]
