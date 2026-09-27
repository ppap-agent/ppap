import json
import unittest
import uuid

import tests.pathsetup  # noqa: F401
from ppap_core.file_analysis import analyze_file, latest_rule_versions
from ppap_core.rule_matching import RuleMatchingAIService
from tests.unit.test_rules import RuleApiTests, excel_rule, rule_body


class AnalysisAI:
    def __init__(self, fail=False):
        self.fail = fail
        self.requests = []

    def structured_completion(self, **request):
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("private provider detail")
        return json.dumps({"customer": "测试客户", "model": "M1", "part": ["零件A"]})


class FileAnalysisTests(unittest.TestCase):
    def setUp(self):
        RuleMatchingAIService.clear_cache()

    def test_latest_is_per_rule_family_not_per_document_type(self):
        rows = [
            {"id": "a", "title": "控制计划", "created_at": "1"},
            {"id": "b", "title": "控制计划", "predecessor_id": "a", "created_at": "2"},
            {"id": "c", "title": "另一客户控制计划", "created_at": "1"},
        ]
        self.assertEqual({r["id"] for r in latest_rule_versions(rows)}, {"b", "c"})

    def test_real_excel_extraction_and_existing_filename_match(self):
        ai = AnalysisAI()
        rules = [{"id": "r1", "title": "控制计划规则", "kind": "engine", "type": "控制计划"}]
        result = analyze_file("控制计划.xlsx", excel_rule(), rules, ai=ai)
        self.assertEqual(result["metadata"]["customer"], "测试客户")
        self.assertEqual(result["metadata"]["part"], "零件A")
        self.assertEqual(result["matched_rule_id"], "r1")
        self.assertEqual(len(ai.requests), 1)
        self.assertEqual(ai.requests[0]["retry_limit"], 0)

    def test_metadata_failure_does_not_hide_deterministic_match(self):
        result = analyze_file("控制计划.xlsx", excel_rule(), [{"id": "r", "title": "控制计划", "type": "控制计划", "kind": "engine"}], ai=AnalysisAI(True))
        self.assertEqual(result["matched_rule_id"], "r")
        self.assertIn("metadata_extraction_failed", result["notices"])
        self.assertNotIn("private", json.dumps(result))


class FileAnalysisApiTests(RuleApiTests):
    def test_analysis_job_uses_existing_file_authorization_and_result_storage(self):
        from ppap_worker import process_claimed

        source = self.upload()
        published = self.client.post("/rules", json={"title": "控制计划规则", "body": rule_body(), "source_file_version_id": source["id"]})
        self.assertEqual(published.status_code, 201)
        body = {"request_id": str(uuid.uuid4()), "source_file_id": source["file_id"], "source_file_version_id": source["id"]}
        accepted = self.client.post("/file-analyses", json=body)
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(self.client.post("/file-analyses", json=body).json()["id"], body["request_id"])
        job = self.store.claim_job("analysis-worker")
        process_claimed(self.store, job, "analysis-worker", rule_ai=AnalysisAI())
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
