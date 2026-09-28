"""PSW PDF evidence tracing must not crash when file_metadata is missing."""
from types import SimpleNamespace
import unittest

from ppap_core.contracts import AuditResultItem


class _Audit:
    def __init__(self, file_metadata):
        self.file_url = "file://psw.pdf"
        self.file_metadata = file_metadata

    def model_copy(self, update):
        return _Audit(update.get("file_metadata", self.file_metadata))


class PswPdfEvidenceTests(unittest.TestCase):
    def test_trace_result_accepts_missing_file_metadata(self):
        from ppap_excel_audit_engine.checkers.psw_pdf_evidence import PswPdfEvidenceMixin

        class Agent(PswPdfEvidenceMixin):
            def _host(self):
                return SimpleNamespace(audit_file=_Audit(None))

        traced = Agent()._trace_result(
            AuditResultItem(
                classification="规则审核",
                item="零件提交保证书 - 规则3",
                priority="P0",
                status="ERR",
                suggestions="vision failed",
            ),
            rule_no=3,
            used="none",
        )
        self.assertEqual(traced.status, "ERR")
        self.assertIsNotNone(traced.evidence)
