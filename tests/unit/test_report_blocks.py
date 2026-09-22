import unittest
from unittest.mock import Mock
import httpx

import tests.pathsetup  # noqa: F401
from ppap_core.feishu_writeback import FeishuWritebackError, report_blocks, create_report_document, append_report_blocks


class ReportBlocksTests(unittest.TestCase):
    def test_missing_user_consent_is_a_definite_rejection_even_with_http_400(self):
        http = Mock()
        http.post.return_value = httpx.Response(400, json={'code': 99991679})
        with self.assertRaisesRegex(FeishuWritebackError, 'feishu_report_create_rejected:99991679'):
            create_report_document(http, 'test-token', '报告')
        self.assertEqual(http.post.call_count, 1)

    def test_permission_rejection_retains_public_error_code(self):
        http = Mock()
        http.post.return_value = httpx.Response(403, json={'code': 99991672, 'msg': 'private provider details'})
        with self.assertRaisesRegex(FeishuWritebackError, 'feishu_report_create_rejected:99991672') as raised:
            create_report_document(http, 'test-token', '报告')
        self.assertNotIn('private provider details', str(raised.exception))
        self.assertEqual(http.post.call_count, 1)

    def test_document_creation_and_ordered_batches(self):
        http = Mock()
        http.post.return_value.status_code = 200
        http.post.return_value.json.return_value = {"code": 0, "data": {"document": {"document_id": "doc123"}}}
        self.assertEqual(create_report_document(http, "test-token", "报告"), "doc123")
        http.reset_mock()
        blocks = [{"block_type": 2}] * 101
        append_report_blocks(http, "test-token", "doc123", blocks, "operation1")
        self.assertEqual([len(call.kwargs["json"]["children"]) for call in http.post.call_args_list], [50, 50, 1])
        self.assertEqual(len({call.kwargs["params"]["client_token"] for call in http.post.call_args_list}), 3)

    def test_ambiguous_creation_is_not_retried(self):
        http = Mock()
        http.post.side_effect = TimeoutError()
        with self.assertRaisesRegex(FeishuWritebackError, "create_unknown"):
            create_report_document(http, "test-token", "报告")
        self.assertEqual(http.post.call_count, 1)

    def body(self):
        return {"execution": "completed", "inputs": [
            {"role": "target", "file_version_id": "v1", "original_filename": "audit.xlsx", "rule_version_id":"rule1", "rule":{"title":"客户规则"}},
            {"role": "reference", "file_version_id": "v2", "original_filename": "reference.xlsx"}],
            "findings": [{"status": "NG", "item": "签字", "suggestions": "缺少签字",
                          "locator": {"file_version_id": "v1", "locator": "Sheet1!A1",
                                      "evidence": {"sources": [{"locator": "Sheet1!A1"}, {"locator": "image 2"}]}},
                          "review": {"judgment": "接受", "rationale": "已核实", "next_step": "补签"}}]}

    def test_preserves_machine_review_evidence_and_reference(self):
        blocks = report_blocks(self.body(), "https://example.test/?round=1")
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        for expected in ("NG · 签字", "人工复核：接受", "理由：已核实", "下一步：补签", "Sheet1!A1；image 2", "参考资料（不单独审核）：reference.xlsx", "匹配规则：客户规则", "待审文件版本：v1", "规则版本：rule1", "https://example.test/?round=1"):
            self.assertIn(expected, text)
        for expected in ('本轮摘要', '不符合 1', '未记录人工复核 0 项', '本报告不代表正式批准', '未自动检查的规则条目会单独列出'):
            self.assertIn(expected, text)

    def test_no_complete_report_from_error_or_missing_results(self):
        for findings in ([], [{"status": "ERR", "locator": {"file_version_id": "v1"}}]):
            body = self.body()
            body["findings"] = findings
            with self.assertRaises(FeishuWritebackError):
                report_blocks(body)
