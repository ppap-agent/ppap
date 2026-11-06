"""Small product-boundary checks for browsing, excerpts and history."""

import io
import os
import unittest
from unittest.mock import Mock, patch

import httpx
import pytest
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
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def test_round_findings_follow_effective_clause_order_not_lexical_ids(self):
        from contextlib import nullcontext

        from product.store import AuthStore

        clauses = [{"id": str(number)} for number in (1, 2, 6, 10, 11)]
        inputs = [("target", "file-version", "file", "report.xlsx", "ready", "rule-version", "客户规则", {"rules": clauses})]
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
                result.fetchone.return_value = ("round", "completed", {}, None, None, 1, None, "deep", {}, False, None)
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

    def setUp(self):
        from api import create_app
        from fastapi.testclient import TestClient
        from product.settings import Settings

        clean = {k: v for k, v in os.environ.items() if not k.startswith(("PPAP_", "FEISHU_", "LLM_", "OPENAI_"))}
        env = patch.dict(os.environ, clean, clear=True)
        env.start()
        self.addCleanup(env.stop)
        settings = Settings(usage_charge_enabled=False, git_sha="a" * 40)
        self.store = self.auth_store(settings)
        self.store.seed_isolated_dev()
        self.client = TestClient(create_app(settings, lambda _: {"state": "ok"}, lambda _: {"state": "ok"}, self.store))
        self.addCleanup(self.client.close)
        self.login("alice")

    def login(self, subject):
        self.client.cookies.clear()
        self.client.post("/isolated-dev/sessions", json={"subject": subject})

    def test_draft_resume_is_paginated_authorized_and_keeps_reaudit_origin(self):
        from product.fixtures import RULE_A

        self.client.post("/drafts")  # Empty drafts should not clutter the homepage.
        version = self.client.post("/files", json={"original_filename": "resume.txt", "byte_size": 1}).json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        self.client.put(path + "/blob", content=b"x")
        self.client.post(path + "/finalize")
        items = [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]
        ids = []
        for _ in range(2):
            draft = self.client.post("/drafts").json()["id"]
            self.client.put(f"/drafts/{draft}", json={"items": items})
            ids.append(draft)
        page = self.client.get("/drafts?limit=1").json()
        tail = self.client.get("/drafts?limit=1&offset=" + str(page["next_offset"])).json()
        self.assertEqual({page["drafts"][0]["id"], tail["drafts"][0]["id"]}, set(ids))
        self.assertIsNone(tail["next_offset"])
        first = self.client.post(f"/drafts/{ids[0]}/accept", json={"idempotency_key": "resume-origin"}).json()["id"]
        next_draft = self.client.post("/drafts", json={"predecessor_id": first}).json()["id"]
        self.client.put(f"/drafts/{next_draft}", json={"items": items})
        listed = self.client.get("/drafts").json()["drafts"]
        self.assertNotIn(ids[0], [row["id"] for row in listed])
        self.assertEqual(next(row for row in listed if row["id"] == next_draft)["predecessor_id"], first)
        # A resumed draft does not depend on a browser query parameter for lineage.
        accepted = self.client.post(f"/drafts/{next_draft}/accept", json={"idempotency_key": "resume-again"}).json()
        self.assertEqual(accepted["predecessor_id"], first)
        self.login("bob")
        self.assertEqual(self.client.get("/drafts").json()["drafts"], [])
        resumed = self.client.post("/drafts", json={"predecessor_id": first})
        self.assertEqual(resumed.status_code, 201)
        self.assertEqual(resumed.json()["predecessor_id"], first)
        self.assertEqual(self.client.get("/drafts").json()["drafts"], [])
        self.login("carol")
        self.assertEqual(self.client.get("/drafts").json()["drafts"], [])
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/drafts").status_code, 401)

    def test_preview_authorization_and_search_across_history_pages(self):
        from product.fixtures import RULE_A

        rounds = []
        for filename in ("first.txt", "second.txt"):
            version = self.client.post("/files", json={"original_filename": filename, "byte_size": 4}).json()
            path = f"/files/{version['file_id']}/versions/{version['id']}"
            self.client.put(path + "/blob", content=b"test")
            self.client.post(path + "/finalize")
            self.assertEqual(self.client.get(path + "/preview").json()["text"], "test")
            draft = self.client.post("/drafts").json()["id"]
            self.client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": version["id"], "role": "target", "rule_version_id": RULE_A}]})
            rounds.append(self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": filename}).json()["id"])
        first = self.client.get("/rounds?limit=1").json()
        second = self.client.get(f"/rounds?limit=1&offset={first['next_offset']}").json()
        self.assertEqual({first["rounds"][0]["id"], second["rounds"][0]["id"]}, set(rounds))
        self.assertIsNone(second["next_offset"])
        self.assertEqual(len(self.client.get("/rounds?q=first").json()["rounds"]), 1)
        round_path = f"/rounds/{rounds[-1]}" + path + "/preview"
        self.assertEqual(self.client.get(round_path).status_code, 200)
        self.assertEqual(self.client.get(round_path + "?start_row=0").status_code, 422)
        self.assertEqual(self.client.get(round_path + "?start_row=1048577").status_code, 422)
        self.login("bob")
        self.assertEqual(self.client.get(path + "/preview").status_code, 403)
        self.assertEqual(self.client.get(round_path).status_code, 200)
        self.assertEqual(self.client.get(round_path + "?sheet=Sheet1&start_row=30").status_code, 200)
        self.assertEqual({row["id"] for row in self.client.get("/rounds").json()["rounds"]}, set(rounds))
