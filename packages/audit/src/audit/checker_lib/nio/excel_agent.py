"""NIO's Excel reader: the shared Excel agent plus NIO's related-part correspondence."""

from typing import Any

from openpyxl import Workbook

from audit.checker_lib.excel_agent import ExcelAgent as _SharedExcelAgent
from audit.checker_lib.excel_agent import RuleEvidencePhase, rule
from audit.engine.rule_engine import RuleContext

from .cross_file_extractors import REFERENCE_IDENTITY_UNRESOLVED, check_reference_identity

__all__ = ["ExcelAgent", "RuleEvidencePhase", "rule"]


class ExcelAgent(_SharedExcelAgent):
    """A related file must name the same part as the audited file before rows are compared."""

    def load_related_workbook(self, item: Any) -> Workbook:
        workbook = super().load_related_workbook(item)
        if self._shared_rule_context is not None:
            check_reference_identity(self._shared_rule_context, self._shared_rule_context.workbook, workbook)
        return workbook

    def _apply_scope(self, status: str, message: str, ctx: RuleContext) -> tuple[str, str]:
        if not type(self).CLAUSE_BOUND_CHECKS and status == "PASS" and ctx.has_unresolved(REFERENCE_IDENTITY_UNRESOLVED):
            return "UNABLE", REFERENCE_IDENTITY_UNRESOLVED
        return super()._apply_scope(status, message, ctx)
