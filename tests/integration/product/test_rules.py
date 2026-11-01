"""Rule preparation through real file extraction and Product API boundaries."""

from __future__ import annotations

import io
import json
import os
import unittest
import uuid
from unittest.mock import patch

import pytest


def rule_body():
    return {
        "kind": "engine",
        "rule_profile": "v3",
        "type": "控制计划",
        "customers": ["测试客户"],
        "models": ["M1"],
        "parts": ["P1"],
        "rules": [{"id": "1", "category": "基本信息", "priority": "P0", "rule_to_check": "必须填写零件号", "type_of_rule": "table"}],
    }


class FakeRuleAI:
    def __init__(self, response=None):
        self.response = json.dumps(response if response is not None else rule_body(), ensure_ascii=False)
        self.requests = []

    def structured_completion(self, **request):
        self.requests.append(request)
        if request["operation"] == "document_classification":
            return "控制计划"
        return self.response


def excel_rule():
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["规则编号", "检查要求"])
    workbook.active.append([1, "必须填写零件号"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class RuleParsingTests(unittest.TestCase):
    def test_excel_rules_use_real_extraction_and_preserve_metadata(self):
        from worker.intake import parse_rule_file

        ai = FakeRuleAI()
        body = parse_rule_file("控制计划规则.xlsx", excel_rule(), ai=ai)
        self.assertEqual(body, rule_body())
        self.assertIn("必须填写零件号", ai.requests[0]["user_prompt"])
        self.assertIn("<SHEET name=", ai.requests[0]["user_prompt"])
        self.assertEqual(len(ai.requests), 1)
        self.assertEqual(ai.requests[0]["request_timeout"], 120)
        self.assertEqual(ai.requests[0]["retry_limit"], 0)

    def test_word_rules_use_the_actual_word_extractor(self):
        from docx import Document
        from worker.intake import parse_rule_file

        document = Document()
        document.add_paragraph("控制计划规则：必须填写零件号")
        stream = io.BytesIO()
        document.save(stream)
        ai = FakeRuleAI()
        body = parse_rule_file("审核条款.docx", stream.getvalue(), ai=ai)
        self.assertEqual(body["rules"][0]["rule_to_check"], "必须填写零件号")
        self.assertEqual([row["operation"] for row in ai.requests], ["document_classification", "rule_extraction"])
        self.assertIn("控制计划规则：必须填写零件号", ai.requests[-1]["user_prompt"])

    def test_json_import_and_parse_failures_do_not_make_empty_professional_rules(self):
        from worker.intake import parse_rule_file
        from product.rules import RuleParseError

        ai = FakeRuleAI()
        self.assertEqual(parse_rule_file("rules.json", json.dumps(rule_body()).encode(), ai=ai), rule_body())
        self.assertEqual(ai.requests, [])
        for response in ({"rules": []}, {"rules": ["bad"]}, {"rules": [{"id": "1"}]}, "private-source-marker"):
            with self.subTest(response=type(response).__name__):
                with self.assertRaisesRegex(RuleParseError, "^rule_parse_failed$"):
                    parse_rule_file("控制计划.md", b"rules", ai=FakeRuleAI(response))

    def test_long_input_is_not_silently_truncated(self):
        from worker.intake import parse_rule_file
        from product.rules import RuleParseError

        ai = FakeRuleAI()
        with self.assertRaisesRegex(RuleParseError, "rule_document_too_long"):
            parse_rule_file("控制计划.md", b"x" * 20001, ai=ai)
        with self.assertRaisesRegex(RuleParseError, "rule_type_required"):
            parse_rule_file("rules.md", b"x" * 4001, ai=ai)
        self.assertEqual(ai.requests, [])


class RuleApiTests(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def _owner_store(self, auth_store_factory):
        self.auth_store = auth_store_factory

    def setUp(self):
        from api import create_app
        from fastapi.testclient import TestClient
        from product.settings import Settings

        env = {key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "PPAP_", "OPENAI_", "MODEL_NAME", "FEISHU_"))}
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.settings = Settings(usage_charge_enabled=False)
        self.store = self.auth_store(self.settings)
        self.store.seed_isolated_dev()
        self.ai = FakeRuleAI()
        self.ai_boundary = patch("audit.engine.checkers.client.AIClient", return_value=self.ai)
        self.ai_boundary.start()
        self.client = TestClient(create_app(self.settings, lambda _: {"state": "ok"}, lambda _: {"state": "ok"}, self.store))
        self.login("alice")

    def tearDown(self):
        self.client.close()
        self.ai_boundary.stop()
        self.env.stop()

    def _scalar(self, query: str, params=()):
        with self.store._conn() as conn:
            return conn.execute(query, params).fetchone()[0]

    def _execute(self, query: str, params=()) -> None:
        with self.store._conn() as conn:
            conn.execute(query, params)

    def login(self, subject):
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/isolated-dev/sessions", json={"subject": subject}).status_code, 200)

    def upload(self, filename="控制计划规则.xlsx", content=None):
        content = excel_rule() if content is None else content
        version = self.client.post("/files", json={"original_filename": filename, "content_type": "application/octet-stream", "byte_size": len(content)}).json()
        path = f"/files/{version['file_id']}/versions/{version['id']}"
        self.assertEqual(self.client.put(path + "/blob", content=content).status_code, 200)
        self.assertEqual(self.client.post(path + "/finalize").status_code, 200)
        return version

    def submit_parse(self, source, request_id=None, **fields):
        return self.client.post("/rules/parse", json={"request_id": request_id or str(uuid.uuid4()), "source_file_id": source["file_id"], "source_file_version_id": source["id"], **fields})

    def finish_parse(self, source):
        from worker import process_claimed

        accepted = self.submit_parse(source)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.ai.requests, [])
        job = self.store.claim_job("rule-worker")
        self.assertEqual(job.id, accepted.json()["id"])
        process_claimed(self.store, job, "rule-worker", rule_ai=self.ai)
        return self.client.get("/rules/parse/" + job.id).json()

    def test_original_sources_and_publication_use_the_same_fixed_version(self):
        source = self.upload()
        path = f"/files/{source['file_id']}/versions/{source['id']}/rule-sources"
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        units = response.json()["units"]
        unit = next(unit for unit in units if unit["excerpt"] == "必须填写零件号")
        self.assertEqual(unit["location"], "Sheet · B2")
        candidate = self.finish_parse(source)["result"]
        clause = {
            "id": "1",
            "title": "基本信息",
            "text": "必须填写零件号",
            "source": source["original_filename"],
            "original": unit["excerpt"],
            "location": unit["location"],
            "checked": True,
            "provenance": {**unit, "fileVersionId": source["id"]},
        }

        def payload(row):
            return {**candidate, "request_id": str(uuid.uuid4()), "case_context": {"registration": json.dumps({"basis": {"source": {"filename": source["original_filename"]}, "clauses": [row]}})}}

        for changed in ({**clause, "location": "已核对原件"}, {**clause, "checked": False}, {**clause, "provenance": {**unit, "fileVersionId": str(uuid.uuid4())}}):
            rejected = self.client.post("/rules", json=payload(changed))
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json()["error"], "rule_source_unverified")
        self.assertEqual(self.client.post("/rules", json=payload(clause)).status_code, 201)
        self.login("bob")
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post("/rules", json=payload(clause)).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(path).status_code, 401)

    def test_customer_parses_reviews_and_publishes_an_immutable_rule_version(self):
        source = self.upload()
        before = self.client.get("/rules").json()["rules"]
        preview = self.finish_parse(source)
        self.assertEqual(preview["status"], "succeeded")
        self.assertEqual(self.client.get("/rules").json()["rules"], before)
        candidate = preview["result"]
        candidate["body"]["rules"][0]["rule_to_check"] = "必须填写零件号和版本"
        published = self.client.post("/rules", json=candidate)
        self.assertEqual(published.status_code, 201)
        version = published.json()
        loaded = self.client.get("/rules/" + version["id"]).json()
        self.assertEqual(loaded["body"]["rules"][0]["rule_to_check"], "必须填写零件号和版本")
        self.assertEqual(loaded["source_file_version_id"], source["id"])
        self.assertEqual(loaded["body"]["customers"], ["测试客户"])
        self.assertEqual(loaded["body"]["kind"], "engine")
        listed = self.client.get("/rules").json()["rules"]
        self.assertEqual(listed[0]["id"], version["id"])
        self.assertEqual(listed[0]["type"], "控制计划")
        self.assertEqual(self.client.put("/rules/" + version["id"], json=candidate).status_code, 405)

    def test_publishing_a_new_version_keeps_the_old_rule_and_round_unchanged(self):
        source = self.upload()
        original = self.client.post("/rules", json={"title": "控制计划", "body": rule_body(), "source_file_version_id": source["id"]}).json()
        target = self.upload("控制计划.xlsx")
        draft = self.client.post("/drafts").json()["id"]
        self.client.put(f"/drafts/{draft}", json={"items": [{"file_version_id": target["id"], "role": "target", "rule_version_id": original["id"]}]})
        round_id = self.client.post(f"/drafts/{draft}/accept", json={"idempotency_key": "old-rule-round"}).json()["id"]
        changed = rule_body()
        changed["rules"][0]["rule_to_check"] = "新版要求"
        successor = self.client.post("/rules", json={"title": "控制计划", "body": changed, "source_file_version_id": source["id"], "predecessor_id": original["id"]})
        self.assertEqual(successor.status_code, 201)
        self.assertNotEqual(successor.json()["id"], original["id"])
        self.assertEqual(successor.json()["predecessor_id"], original["id"])
        self.assertEqual(self.client.get("/rules/" + original["id"]).json()["body"], rule_body())
        self.assertEqual(self.client.get("/rounds/" + round_id).json()["inputs"][0]["rule"]["id"], original["id"])

    def test_source_authorization_and_enterprise_rule_visibility_are_separate(self):
        source = self.upload()
        payload = {"title": "控制计划", "body": rule_body(), "source_file_version_id": source["id"]}
        published = self.client.post("/rules", json=payload).json()
        self.login("bob")
        self.assertEqual(self.client.get("/rules/" + published["id"]).status_code, 200)
        self.assertEqual(self.client.get(f"/files/{source['file_id']}/versions/{source['id']}/download").status_code, 403)
        self.assertEqual(self.client.post("/rules", json=payload).status_code, 403)
        self.assertEqual(self.submit_parse(source).status_code, 403)
        self.login("carol")
        self.assertEqual(self.client.get("/rules/" + published["id"]).status_code, 403)
        self.assertEqual(self.client.post("/rules", json=payload).status_code, 403)
        other = self.upload()
        self.assertEqual(self.client.post("/rules", json={**payload, "source_file_version_id": other["id"], "predecessor_id": published["id"]}).status_code, 403)
        self.assertNotIn(published["id"], [row["id"] for row in self.client.get("/rules").json()["rules"]])

    def test_invalid_rule_bodies_fail_without_publishing_or_echoing_source(self):
        source = self.upload()
        before = self.client.get("/rules").json()["rules"]
        for changes in ({"kind": "presence"}, {"rules": []}, {"type": "unknown"}, {"rule_profile": {}}, {"customers": [None]}, {"rules": ["private-source-marker"]}):
            with self.subTest(field=next(iter(changes))):
                response = self.client.post("/rules", json={"title": "控制计划", "body": {**rule_body(), **changes}, "source_file_version_id": source["id"]})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("private-source-marker", response.text)
        self.assertEqual(self.client.get("/rules").json()["rules"], before)

    def test_failed_parsing_does_not_publish_and_does_not_echo_provider_data(self):
        source = self.upload("控制计划.md", b"private-source-marker")
        self.ai.response = "private-provider-marker"
        before = self.client.get("/rules").json()["rules"]
        response = self.finish_parse(source)
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["result"], {"error": "rule_parse_failed"})
        self.assertEqual(self.client.get("/rules").json()["rules"], before)

    def test_json_type_conflict_is_recoverable_with_the_same_original(self):
        from worker import process_claimed

        content = json.dumps(rule_body()).encode()
        source = self.upload("rules.json", content)
        before = self.client.get("/rules").json()["rules"]
        receipts = []
        for document_type in ("全功能检验报告", "控制计划"):
            accepted = self.submit_parse(source, document_type=document_type)
            self.assertEqual(accepted.status_code, 202)
            job = self.store.claim_job("rule-worker")
            self.assertEqual(job.id, accepted.json()["id"])
            process_claimed(self.store, job, "rule-worker", rule_ai=self.ai)
            receipt = self.client.get("/rules/parse/" + job.id).json()
            self.assertEqual(receipt["source_file_id"], source["file_id"])
            self.assertEqual(receipt["source_file_version_id"], source["id"])
            self.assertEqual(receipt["document_type"], document_type)
            receipts.append(receipt)
        self.assertNotEqual(receipts[0]["id"], receipts[1]["id"])
        self.assertEqual(receipts[0]["status"], "failed")
        self.assertEqual(receipts[0]["result"], {"error": "rule_document_type_mismatch"})
        self.assertEqual(receipts[1]["status"], "succeeded")
        self.assertEqual(receipts[1]["result"]["body"], rule_body())
        self.assertEqual(self.client.get("/rules").json()["rules"], before)
        self.assertEqual(self.client.get(f"/files/{source['file_id']}/versions/{source['id']}/download").content, content)
        self.assertEqual(self.ai.requests, [])

    def test_invalid_request_shapes_do_not_echo_payloads(self):
        for path in ("/rules", "/rules/parse"):
            with self.subTest(path=path):
                response = self.client.post(path, json=["private-request-marker"])
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("private-request-marker", response.text)

    def test_unready_sources_and_unauthenticated_requests_are_rejected(self):
        source = self.client.post("/files", json={"original_filename": "控制计划.xlsx", "content_type": "application/octet-stream", "byte_size": 10}).json()
        requests = [
            ("/rules/parse", {"request_id": str(uuid.uuid4()), "source_file_id": source["file_id"], "source_file_version_id": source["id"]}),
            ("/rules", {"title": "控制计划", "body": rule_body(), "source_file_version_id": source["id"]}),
        ]
        for path, payload in requests:
            self.assertEqual(self.client.post(path, json=payload).status_code, 409)
        self.client.cookies.clear()
        for path, payload in requests:
            self.assertEqual(self.client.post(path, json=payload).status_code, 401)
        self.assertEqual(self.ai.requests, [])

    def test_same_request_resumes_one_job_and_unknown_is_not_automatically_reexecuted(self):
        from worker import process_claimed

        source = self.upload()
        request_id = str(uuid.uuid4())
        accepted = self.submit_parse(source, request_id)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.submit_parse(source, request_id).json()["id"], request_id)
        self.assertEqual(self._scalar("SELECT count(*) FROM engine.jobs"), 1)
        self.assertEqual(self.submit_parse(source, request_id, document_type="控制计划").status_code, 409)
        job = self.store.claim_job("first")
        self.assertFalse(self.store.renew_job_lease(job, "other"))
        self.assertTrue(self.store.renew_job_lease(job, "first"))
        self._execute(
            "UPDATE engine.jobs SET lease_until = now() - interval '1 second' WHERE id = %s",
            (job.id,),
        )
        self.assertEqual(self.client.get("/rules/parse/" + request_id).json()["status"], "unknown")
        self.assertIsNone(self.store.claim_job("second"))
        self.assertEqual(self.submit_parse(source, request_id).json()["status"], "unknown")
        self.assertEqual(self.ai.requests, [])
        process_claimed(self.store, job, "first", rule_ai=self.ai)
        loaded = self.client.get("/rules/parse/" + request_id).json()
        self.assertEqual(loaded["status"], "succeeded")
        self.assertEqual(loaded["result"]["body"], rule_body())
        self.assertFalse(self.store.complete_job(job, "other", {"error": "late"}, "failed"))
        self.assertEqual(self.client.get("/rules/parse/" + request_id).json(), loaded)
        second = self.submit_parse(source)
        self.assertNotEqual(second.json()["id"], request_id)
        self.assertEqual(self._scalar("SELECT count(*) FROM engine.jobs"), 2)

    def test_parse_result_requires_current_source_access_and_same_tenant(self):
        source = self.upload()
        completed = self.finish_parse(source)
        self.login("bob")
        self.assertEqual(self.client.get("/rules/parse/" + completed["id"]).status_code, 403)
        self.login("carol")
        self.assertEqual(self.client.get("/rules/parse/" + completed["id"]).status_code, 403)
        self.assertEqual(self.submit_parse(self.upload(), completed["id"]).status_code, 403)
        self.login("alice")
        self._execute(
            "DELETE FROM product.resource_grants WHERE resource_id = %s AND user_id = %s",
            (source["file_id"], self.store.principal_for_fixture("alice").user_id),
        )
        self.assertEqual(self.client.get("/rules/parse/" + completed["id"]).status_code, 403)


class FileAnalysisAI:
    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    def structured_completion(self, **request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("private provider detail")
        return json.dumps({"customer": "测试客户", "model": "M1", "part": ["零件A"]})


class FileAnalysisApiTests(RuleApiTests):
    def test_analysis_job_uses_existing_file_authorization_and_result_storage(self):
        from worker import process_claimed

        source = self.upload()
        published = self.client.post("/rules", json={"title": "控制计划规则", "body": rule_body(), "source_file_version_id": source["id"]})
        self.assertEqual(published.status_code, 201)
        body = {"request_id": str(uuid.uuid4()), "source_file_id": source["file_id"], "source_file_version_id": source["id"]}
        accepted = self.client.post("/file-analyses", json=body)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.client.post("/file-analyses", json=body).json()["id"], body["request_id"])
        job = self.store.claim_job("analysis-worker")
        process_claimed(self.store, job, "analysis-worker", rule_ai=FileAnalysisAI())
        result = self.client.get("/file-analyses/" + job.id).json()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["matched_rule_id"], published.json()["id"])
        latest_path = f"/files/{source['file_id']}/versions/{source['id']}/analysis"
        self.assertEqual(self.client.get(latest_path).json()["analysis"]["id"], job.id)
        for subject in ("bob", "carol"):
            self.login(subject)
            self.assertEqual(self.client.get("/file-analyses/" + job.id).status_code, 403)
            self.assertEqual(self.client.get(latest_path).status_code, 403)
            self.assertEqual(self.client.post("/file-analyses", json=body).status_code, 403)


if __name__ == "__main__":
    unittest.main()
