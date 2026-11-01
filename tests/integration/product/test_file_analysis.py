import io
import json
import unittest

from product.rules import latest_rule_versions
from worker.intake import analyze_file
from product.rule_matching import RuleMatchingAIService


def excel_rule():
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["规则编号", "检查要求"])
    workbook.active.append([1, "必须填写零件号"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


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
