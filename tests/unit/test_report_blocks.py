import unittest

import tests.pathsetup  # noqa: F401
from ppap_core.feishu_writeback import FeishuWritebackError, report_blocks


class ReportBlocksTests(unittest.TestCase):
    def body(self):
        return {"execution": "completed", "inputs": [
            {"role": "target", "file_version_id": "v1", "original_filename": "audit.xlsx"},
            {"role": "reference", "file_version_id": "v2", "original_filename": "reference.xlsx"}],
            "findings": [{"status": "NG", "item": "签字", "suggestions": "缺少签字",
                          "locator": {"file_version_id": "v1", "locator": "Sheet1!A1",
                                      "evidence": {"sources": [{"locator": "Sheet1!A1"}, {"locator": "image 2"}]}},
                          "review": {"judgment": "接受", "rationale": "已核实", "next_step": "补签"}}]}

    def test_preserves_machine_review_evidence_and_reference(self):
        blocks = report_blocks(self.body(), "https://example.test/?round=1")
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        for expected in ("NG · 签字", "人工复核：接受", "理由：已核实", "下一步：补签", "Sheet1!A1；image 2", "参考资料（不单独审核）：reference.xlsx", "https://example.test/?round=1"):
            self.assertIn(expected, text)

    def test_no_complete_report_from_error_or_missing_results(self):
        for findings in ([], [{"status": "ERR", "locator": {"file_version_id": "v1"}}]):
            body = self.body()
            body["findings"] = findings
            with self.assertRaises(FeishuWritebackError):
                report_blocks(body)
