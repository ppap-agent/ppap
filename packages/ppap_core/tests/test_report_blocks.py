import unittest
from unittest.mock import Mock
import httpx

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
            "findings": [{"status": "NG", "item": "签字", "classification": "资料", "priority": "P0", "suggestions": "缺少签字",
                          "locator": {"file_version_id": "v1", "locator": "Sheet1!A1",
                                      "evidence": {"sources": [{"locator": "Sheet1!A1"}, {"locator": "image 2"}]}},
                          "review": {"judgment": "接受", "rationale": "已核实", "next_step": "补签"}}]}

    def test_preserves_machine_review_evidence_and_reference(self):
        blocks = report_blocks(self.body(), "https://example.test/?round=1")
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        for expected in ("NG · 1 · 资料", "检查项正文：签字", "分类/优先级：资料 · P0", "人工复核：接受", "理由：已核实", "下一步：补签", "Sheet1!A1；image 2", "参考资料（不单独审核）：reference.xlsx", "匹配规则：客户规则", "待审文件版本：v1", "规则版本：rule1", "https://example.test/?round=1"):
            self.assertIn(expected, text)
        for expected in ('本轮摘要', '不符合 1', '未记录人工复核 0 项', '本报告不代表正式批准', '未自动检查的规则条目会单独列出'):
            self.assertIn(expected, text)

    def test_no_complete_report_from_error_or_missing_results(self):
        for findings in ([], [{"status": "ERR", "locator": {"file_version_id": "v1"}}]):
            body = self.body()
            body["findings"] = findings
            with self.assertRaises(FeishuWritebackError):
                report_blocks(body)

    def test_uncovered_clause_without_published_body_stays_visible_once(self):
        body = self.body()
        body["coverage"] = {"rule_items": [
            {"id": "R2", "file_version_id": "v1", "category": "资料", "rule_to_check": "须提交签字记录",
             "coverage": "unsupported", "reason": "manual-only"}],
            "unchecked": ["须提交签字记录（未自动检查：无对应内置检查，需人工核对）"]}
        blocks = report_blocks(body)
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values()
                         if isinstance(value, dict) for part in value["elements"])
        self.assertIn("R2 · 资料：无对应内置检查，需人工核对", text)
        self.assertIn("本轮有效条款：须提交签字记录", text)
        self.assertEqual(text.count("须提交签字记录"), 1)

    def test_standard_finding_body_survives_empty_or_unrelated_rule_list(self):
        cases = [
            ([], True),
            ([{"id": "R2", "rule_to_check": "另一项要求"}], True),
            ([{"id": "R1", "rule_to_check": "另一项要求"}], True),
            ([{"id": "R1", "rule_to_check": "签字"}], False),
        ]
        for clauses, needs_finding_body in cases:
            with self.subTest(clauses=clauses):
                body = self.body()
                body["inputs"][0]["rule"]["body"] = {"rules": clauses}
                body["findings"][0]["locator"]["rule_item_id"] = "R1"
                blocks = report_blocks(body)
                contents = [part["text_run"]["content"] for block in blocks for value in block.values()
                            if isinstance(value, dict) for part in value["elements"]]
                self.assertEqual("检查项正文：签字" in contents, needs_finding_body)

    def test_deep_report_keeps_status_scope_typed_sources_and_rule_authority(self):
        body = {"analysis_mode": "deep", "execution": "completed", "inputs": [
            {"role": "target", "file_version_id": "v1", "original_filename": "plan.xlsx", "rule_version_id": "rv1",
             "rule": {"title": "客户规则", "source_file_version_id": "original-rule-v", "body": {"rules": [{"id": "R1", "rule_to_check": "修订后的频次要求"}]}}},
            {"role": "reference", "file_version_id": "v2", "original_filename": "flow.pdf"},
        ], "findings": [{"status": "UNABLE", "item": "频次检查", "suggestions": "关系未明", "locator": {"file_version_id": "v1", "semantic": {
            "rule_version_id": "rv1", "clause_id": "R1", "rule_text": "修订后的频次要求", "diagnostic": "unresolved_relation",
            "scope": {"total": 4, "checked": 2, "unknown": 2},
            "sources": [{"file_version_id": "v2", "filename": "flow.pdf", "role": "reference", "locator": {"kind": "pdf_block", "page": 3, "block": 2}, "excerpt": "流程记录"}],
        }}}]}
        blocks = report_blocks(body)
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        for expected in (
            "无法判断 1", "不适用 0", "条款核验记录（按条款累计）：共 4 条，已判断 2 条，无法判断 2 条",
            "本条核验记录：共 4 条，已判断 2 条，无法判断 2 条", "UNABLE · R1 · 条款", "修订后的频次要求",
            "规则原件来源版本（仅溯源，不表示修订条款位于原件）：original-rule-v",
            "flow.pdf（参考；文件版本 v2）", "位置：第 3 页 · 文本块 2", "原文摘录：流程记录", "无法判断原因：unresolved_relation",
        ):
            self.assertIn(expected, text)
        self.assertEqual(text.count("修订后的频次要求"), 1)
        body["findings"][0]["locator"]["semantic"]["rule_text"] = "原件中的旧要求"
        with self.assertRaises(FeishuWritebackError):
            report_blocks(body)
        body["findings"][0]["locator"]["semantic"]["rule_text"] = "修订后的频次要求"
        body["findings"][0]["locator"]["semantic"]["sources"][0]["file_version_id"] = "another-version"
        with self.assertRaises(FeishuWritebackError):
            report_blocks(body)

    def test_deep_report_uses_short_titles_readable_sources_and_complete_rule_once(self):
        long_rule = "控制计划的每一项频次必须与流程记录一致，存在未知项时说明无法判断。" * 120
        clauses = [{"id": "R1", "rule_to_check": long_rule},
                   {"id": "R2", "rule_to_check": "签字须完整"},
                   {"id": "R3", "rule_to_check": "未覆盖的要求也须列出"}]
        sources = [
            {"file_version_id": "v1", "filename": "plan.xlsx", "role": "target", "locator": locator, "excerpt": excerpt}
            for locator, excerpt in [
                ({"kind": "xlsx_cell", "sheet": "控制计划", "cell": "B2"}, "频次待确认"),
                ({"kind": "xlsx_range", "sheet": "控制计划", "start": "C3", "end": "D8"}, "C3:D8 记录"),
            ]
        ] + [
            {"file_version_id": version, "filename": filename, "role": "reference", "locator": locator, "excerpt": excerpt}
            for version, filename, locator, excerpt in [
                ("v2", "flow.pdf", {"kind": "pdf_block", "page": 4, "block": 7}, "流程记录"),
                ("v3", "notes.docx", {"kind": "docx_paragraph", "paragraph": 9}, "核对段落"),
                ("v3", "notes.docx", {"kind": "docx_cell", "table": 2, "row": 3, "column": 4}, "核对单元格"),
            ]
        ]
        body = {"analysis_mode": "deep", "execution": "completed", "inputs": [
            {"role": "target", "file_version_id": "v1", "original_filename": "plan.xlsx", "rule_version_id": "rv1",
             "rule": {"title": "客户规则", "source_file_version_id": "original-v", "body": {"rules": clauses}}},
            {"role": "reference", "file_version_id": "v2", "original_filename": "flow.pdf"},
            {"role": "reference", "file_version_id": "v3", "original_filename": "notes.docx"},
        ], "findings": [
            {"status": "UNABLE", "item": long_rule, "classification": "频次", "suggestions": "须补充图例",
             "locator": {"file_version_id": "v1", "semantic": {"rule_version_id": "rv1", "clause_id": "R1",
                 "rule_text": long_rule, "scope": {"total": 6, "checked": 4, "unknown": 2}, "sources": sources}}},
            {"status": "PASS", "item": "签字须完整", "classification": "签字",
             "locator": {"file_version_id": "v1", "semantic": {"rule_version_id": "rv1", "clause_id": "R2",
                 "rule_text": "签字须完整", "scope": {"total": 1, "checked": 1, "unknown": 0}, "sources": []}}},
        ], "coverage": {"rule_items": [
            {"id": "R3", "file_version_id": "v1", "category": "特殊要求", "rule_to_check": "未覆盖的要求也须列出",
             "coverage": "unsupported", "reason": "manual-only"}],
            "unchecked": ["未覆盖的要求也须列出（未自动检查：无对应内置检查，需人工核对）", "flow.pdf（参考资料，不单独审核）"]}}
        blocks = report_blocks(body)
        contents = [part["text_run"]["content"] for block in blocks for value in block.values()
                    if isinstance(value, dict) for part in value["elements"]]
        headings = [block["heading3"]["elements"][0]["text_run"]["content"]
                    for block in blocks if "heading3" in block]
        self.assertEqual(headings, ["UNABLE · R1 · 频次", "PASS · R2 · 签字"])
        text = "\n".join(contents)
        for rule_text in (long_rule, "签字须完整", "未覆盖的要求也须列出"):
            self.assertEqual(text.count(rule_text), 1)
        for expected in ("R3 · 特殊要求：无对应内置检查，需人工核对", "待审文件版本：v1", "规则版本：rv1",
                         "规则原件来源版本（仅溯源，不表示修订条款位于原件）：original-v",
                         "控制计划!B2", "控制计划!C3:D8", "第 4 页 · 文本块 7", "段落 9",
                         "表格 2 · 行 3 · 列 4", "flow.pdf（参考；文件版本 v2）", "原文摘录：核对单元格",
                         "未检查范围：flow.pdf（参考资料，不单独审核）"):
            self.assertIn(expected, text)
        self.assertNotIn('"kind":', text)
