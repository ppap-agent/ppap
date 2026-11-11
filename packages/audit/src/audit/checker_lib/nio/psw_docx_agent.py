"""V3 PSW DOCX audit using the shared Word evidence adapter."""

from audit.engine.checkers.docx_workbook_agent import StructuredDocxWorkbookMixin
from audit.engine.checkers.psw_docx_evidence import PswDocxEvidenceMixin

from .psw_excel_agent import PswExcelAgent


class PswDocxAgent(PswDocxEvidenceMixin, StructuredDocxWorkbookMixin, PswExcelAgent):
    """Reuse strict V3 PSW rules over the same authoritative Word evidence."""


__all__ = ["PswDocxAgent"]
