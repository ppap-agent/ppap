"""Small product-boundary checks for browsing, excerpts and history."""

import io
import unittest
from unittest.mock import Mock

import httpx
from product.feishu_drive import FeishuMetaError, list_drive_folder
from product.preview import document_preview


class PreviewTests(unittest.TestCase):
    def test_sheet_selection_reads_later_sheets_and_preserves_row_coordinates(self):
        from openpyxl import Workbook

        book = Workbook()
        for number in range(6):
            book.create_sheet(f"Data-{number}")
        book["Data-5"]["B235"] = "=1+1"
        book["Data-5"]["C436"] = "later"
        output = io.BytesIO()
        book.save(output)
        book.close()
        content = output.getvalue()
        self.assertIn("Data-5", document_preview(content, "report.xlsm")["sheets"])
        page = document_preview(content, "report.xlsm", "Data-5", 233)
        self.assertEqual(page["sheet"], "Data-5")
        self.assertEqual(page["rows"], [{"number": 235, "text": "B235: =1+1"}])
        self.assertEqual(page["next_row"], 433)
        tail = document_preview(content, "report.xlsm", "Data-5", page["next_row"])
        self.assertIn("C436: later", tail["text"])
        self.assertIsNone(tail["next_row"])
        self.assertFalse(document_preview(content, "report.xlsm", "missing")["available"])

    def test_office_excerpts_preserve_text_cells_and_do_not_evaluate_formulas(self):
        from docx import Document
        from openpyxl import Workbook

        book = Workbook()
        book.active.title = "控制计划"
        book.active.append(["零件号", "=1+1"])
        output = io.BytesIO()
        book.save(output)
        book.close()
        text = document_preview(output.getvalue(), "sample.xlsx")["text"]
        self.assertIn("控制计划", text)
        self.assertIn("A1: 零件号", text)
        self.assertIn("B1: =1+1", text)
        doc = Document()
        doc.add_paragraph("before")
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "<not-html>"
        doc.add_paragraph("after")
        output = io.BytesIO()
        doc.save(output)
        text = document_preview(output.getvalue(), "sample.docx")["text"]
        self.assertLess(text.index("before"), text.index("<not-html>"))
        self.assertLess(text.index("<not-html>"), text.index("after"))
        self.assertFalse(document_preview(b"unsupported", "sample.doc")["available"])

    def test_folder_listing_passes_current_user_auth_and_provider_cursor(self):
        def handler(request):
            self.assertEqual(request.headers["authorization"], "Bearer test-user")
            self.assertEqual(request.url.params["folder_token"], "folder-test")
            self.assertEqual(request.url.params["page_token"], "next-test")
            return httpx.Response(200, json={"code": 0, "data": {"files": [{"token": "file-test", "name": "客户文件", "type": "docx"}], "has_more": True, "next_page_token": "page-two"}})

        with httpx.Client(transport=httpx.MockTransport(handler)) as http:
            page = list_drive_folder(http, "test-user", "folder-test", "next-test")
        self.assertEqual(page["next_page_token"], "page-two")
        self.assertEqual(page["files"][0]["name"], "客户文件")
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))) as http:
            with self.assertRaises(FeishuMetaError):
                list_drive_folder(http, "test-user")


class ProductExperienceTests(unittest.TestCase):
    def test_round_findings_follow_effective_clause_order_not_lexical_ids(self):
        from contextlib import nullcontext

        from product.store import AuthStore

        clauses = [{"id": str(number)} for number in (1, 2, 6, 10, 11)]
        inputs = [
            (
                "target",
                "file-version",
                "file",
                "report.xlsx",
                "ready",
                "rule-version",
                "客户规则",
                {"rules": clauses},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                1,
                "a" * 64,
                None,
            )
        ]
        findings = [
            (
                f"file-version:rule:{number}",
                "检查",
                "正文",
                "P1",
                "NG",
                "依据",
                {"file_version_id": "file-version", "rule_item_id": str(number)},
                "认可" if number == 6 else None,
                "保留复核",
                "",
                2,
                [],
            )
            for number in (1, 10, 11, 2, 6)
        ]

        def execute(sql, params):
            result = Mock()
            result.fetchone.return_value = None
            result.fetchall.return_value = []
            if "FROM product.audit_rounds" in sql:
                result.fetchone.return_value = ("round", "completed", {}, None, None, 1, None, "deep", {}, False, None, [])
            elif "FROM product.round_inputs" in sql:
                result.fetchall.return_value = inputs
            elif "FROM product.findings" in sql:
                result.fetchall.return_value = findings
            return result

        conn = Mock()
        conn.execute.side_effect = execute
        conn.pipeline.return_value = nullcontext()
        body = AuthStore._round_public(object.__new__(AuthStore), conn, "round")
        self.assertEqual([item["locator"]["rule_item_id"] for item in body["findings"]], ["1", "2", "6", "10", "11"])
        self.assertEqual(body["findings"][2]["review"]["judgment"], "认可")
