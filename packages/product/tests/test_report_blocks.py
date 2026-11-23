import unittest
from unittest.mock import Mock

import httpx
from product.feishu_writeback import FeishuWritebackError, append_report_blocks, create_report_document, report_blocks


class ReportBlocksTests(unittest.TestCase):
    def test_missing_user_consent_is_a_definite_rejection_even_with_http_400(self):
        http = Mock()
        http.post.return_value = httpx.Response(400, json={"code": 99991679})
        with self.assertRaisesRegex(FeishuWritebackError, "feishu_report_create_rejected:99991679"):
            create_report_document(http, "test-token", "报告")
        self.assertEqual(http.post.call_count, 1)

    def test_permission_rejection_retains_public_error_code(self):
        http = Mock()
        http.post.return_value = httpx.Response(403, json={"code": 99991672, "msg": "private provider details"})
        with self.assertRaisesRegex(FeishuWritebackError, "feishu_report_create_rejected:99991672") as raised:
            create_report_document(http, "test-token", "报告")
        self.assertNotIn("private provider details", str(raised.exception))
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
        return {
            "execution": "completed",
            "inputs": [
                {"role": "target", "file_version_id": "v1", "original_filename": "audit.xlsx", "rule_version_id": "rule1", "rule": {"title": "客户规则"}},
                {"role": "reference", "file_version_id": "v2", "original_filename": "reference.xlsx"},
            ],
            "findings": [
                {
                    "status": "NG",
                    "item": "签字",
                    "classification": "资料",
                    "priority": "P0",
                    "suggestions": "缺少签字",
                    "locator": {
                        "file_version_id": "v1",
                        "rule_item_id": "R1",
                        "semantic": {
                            "rule_version_id": "rule1",
                            "clause_id": "R1",
                            "rule_text": "签字",
                            "scope": {"total": 1, "checked": 1, "unknown": 0},
                            "sources": [
                                {"file_version_id": "v1", "filename": "audit.xlsx", "role": "target", "locator": {"kind": "xlsx_cell", "sheet": "Sheet1", "cell": "A1"}, "excerpt": "缺少签字"},
                                {"file_version_id": "v2", "filename": "reference.xlsx", "role": "reference", "locator": {"kind": "xlsx_cell", "sheet": "签字页", "cell": "B2"}, "excerpt": "签字栏"},
                            ],
                        },
                    },
                    "review": {"judgment": "接受", "rationale": "已核实", "next_step": "补签"},
                }
            ],
        }

    def test_preserves_machine_review_evidence_and_reference(self):
        blocks = report_blocks(self.body(), "https://example.test/?round=1")
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        for expected in (
            "NG · R1 · 资料",
            "本轮有效条款：R1 · 签字",
            "分类/优先级：资料 · P0",
            "人工复核：接受",
            "理由：已核实",
            "下一步：补签",
            "来源：audit.xlsx（待审）；位置：工作表「Sheet1」A1；原文摘录：缺少签字",
            "来源：reference.xlsx（参考）；位置：工作表「签字页」B2；原文摘录：签字栏",
            "参考资料（不单独审核）：reference.xlsx",
            "匹配规则：客户规则",
            "https://example.test/?round=1",
        ):  # noqa: E501
            self.assertIn(expected, text)
        for expected in ("本轮摘要", "不符合 1", "未记录人工复核 0 项", "本报告不代表正式批准", "未自动检查的规则条目会单独列出"):
            self.assertIn(expected, text)

    def test_the_report_opens_with_what_it_is_and_the_signoff_at_delivery(self):
        """#357: the machine's result stands only as confirmed and signed; the document says whether it was signed when delivered."""

        def opening(body):
            blocks = report_blocks(body)
            return [part["text_run"]["content"] for block in blocks[1:3] for value in block.values() if isinstance(value, dict) for part in value["elements"]]

        self.assertEqual(opening(self.body()), ["机器初审结果，以人工确认和签发为准。", "交付时未签发。"])
        signed = {**self.body(), "signoff": {"state": "signed", "signed_at": "2026-11-22T06:05:00+00:00", "signer": "曹阳"}}
        self.assertEqual(opening(signed), ["机器初审结果，以人工确认和签发为准。", "交付时已签发 · 曹阳 · 2026 年 11 月 22 日 06:05（UTC）"])
        withdrawn = {**self.body(), "signoff": {"state": "unsigned", "signed_at": None, "signer": None}}
        self.assertEqual(opening(withdrawn)[1], "交付时未签发。")

    def test_the_document_is_named_by_task_number_and_part_never_an_internal_id(self):
        from product.feishu_writeback import report_title

        def row(part, selection="selected"):
            return {"selection": selection, "match": {"metadata": {"part": part}}}

        task = {"id": "6b34b769-20ea-5f83-ae34-4788db23cbf2", "number": "PPAP-20261123-008", "files": [row("壳体", "unselected"), row(None), row("节气门壳体")]}
        self.assertEqual(report_title(task), "PPAP 审核报告 · PPAP-20261123-008 · 节气门壳体")
        self.assertEqual(report_title({**task, "files": []}), "PPAP 审核报告 · PPAP-20261123-008")
        self.assertEqual(report_title(None), "PPAP 审核报告")

    def test_the_report_says_whose_rules_and_which_mode_beside_the_signoff(self):
        """#361: the document's top says what the round stands on, as the result page does."""

        def basis(body, customer=""):
            block = report_blocks(body, customer=customer)[3]
            return "".join(part["text_run"]["content"] for value in block.values() if isinstance(value, dict) for part in value["elements"])

        self.assertEqual(basis(self.body(), "UAES"), "客户：联电科技/UAES · 标准模式 · 规则：受理时冻结的版本，每份文件的规则见下文")
        self.assertTrue(basis({**self.body(), "deep_mode": True}, "UAES").startswith("客户：联电科技/UAES · 深度模式"))
        self.assertEqual(basis(self.body()), "标准模式 · 规则：受理时冻结的版本，每份文件的规则见下文")

    def test_no_complete_report_from_error_or_missing_results(self):
        for findings in ([], [{"status": "ERR", "locator": {"file_version_id": "v1"}}]):
            body = self.body()
            body["findings"] = findings
            with self.assertRaises(FeishuWritebackError):
                report_blocks(body)

    def test_pdf_cell_report_preserves_original_page_and_table_coordinates(self):
        body = self.body()
        body["inputs"][0]["original_filename"] = "audit.pdf"
        locator = {"kind": "pdf_cell", "page": 3, "row": 12, "column": 4}
        source = {"file_version_id": "v1", "filename": "audit.pdf", "role": "target", "locator": locator, "excerpt": "缺少签字"}
        body["findings"][0]["locator"] = {
            "file_version_id": "v1",
            "semantic": {"rule_version_id": "rule1", "scope": {"total": 1, "checked": 1, "unknown": 0}, "sources": [source]},
        }
        blocks = report_blocks(body)
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        self.assertIn("audit.pdf（待审）", text)
        self.assertIn("位置：第 3 页表格第 12 行第 4 列", text)
        self.assertIn("原文摘录：缺少签字", text)
        self.assertNotIn("Sheet", text)
        self.assertEqual(body["findings"][0]["status"], "NG")
        invalid = [{**locator, field: value} for field in ("page", "row", "column") for value in (None, True, "1", 1.5, 0, -1)]
        invalid.extend({key: value for key, value in locator.items() if key != field} for field in ("page", "row", "column"))
        invalid.extend(({**locator, "table": 1}, {**locator, "bbox": [0, 0, 1, 1]}))
        for candidate in invalid:
            with self.subTest(locator=candidate), self.assertRaises(FeishuWritebackError):
                source["locator"] = candidate
                report_blocks(body)

    def test_uncovered_clause_without_published_body_stays_visible_once(self):
        body = self.body()
        body["coverage"] = {
            "rule_items": [{"id": "R2", "file_version_id": "v1", "category": "资料", "rule_to_check": "须提交签字记录", "coverage": "unsupported", "reason": "manual-only"}],
            "unchecked": ["须提交签字记录（未自动检查：无对应内置检查，需人工核对）"],
        }
        blocks = report_blocks(body)
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        self.assertIn("R2 · 资料：无对应内置检查，需人工核对", text)
        self.assertIn("本轮有效条款：须提交签字记录", text)
        self.assertEqual(text.count("须提交签字记录"), 1)

    def test_finding_without_semantic_evidence_states_the_gap_instead_of_a_position(self):
        body = self.body()
        body["findings"][0]["locator"] = {"file_version_id": "v1", "rule_item_id": "R1"}
        blocks = report_blocks(body)
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        self.assertIn("依据：结果缺少可核对的证据记录，需核对原始审核记录。", text)
        self.assertNotIn("来源：", text)

    def test_deep_report_keeps_status_scope_typed_sources_and_rule_authority(self):
        body = {
            "executor": "ppap-deep",
            "execution": "completed",
            "inputs": [
                {
                    "role": "target",
                    "file_version_id": "v1",
                    "original_filename": "plan.xlsx",
                    "rule_version_id": "rv1",
                    "rule": {"title": "客户规则", "source_file_version_id": "original-rule-v", "body": {"rules": [{"id": "R1", "rule_to_check": "修订后的频次要求"}]}},
                },
                {"role": "reference", "file_version_id": "v2", "original_filename": "flow.pdf"},
            ],
            "findings": [
                {
                    "status": "UNABLE",
                    "item": "频次检查",
                    "suggestions": "关系未明",
                    "locator": {
                        "file_version_id": "v1",
                        "semantic": {
                            "rule_version_id": "rv1",
                            "clause_id": "R1",
                            "rule_text": "修订后的频次要求",
                            "diagnostic": "unresolved_relation",
                            "scope": {"total": 4, "checked": 2, "unknown": 2},
                            "sources": [{"file_version_id": "v2", "filename": "flow.pdf", "role": "reference", "locator": {"kind": "pdf_block", "page": 3, "block": 2}, "excerpt": "流程记录"}],
                        },
                    },
                }
            ],
        }
        blocks = report_blocks(body)
        text = "\n".join(part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"])
        for expected in (
            "无法判断 1",
            "不适用 0",
            "条款核验记录（按条款累计）：共 4 条，已判断 2 条，无法判断 2 条",
            "本条核验记录：共 4 条，已判断 2 条，无法判断 2 条",
            "UNABLE · R1 · 条款",
            "修订后的频次要求",
            "flow.pdf（参考）",
            "位置：第 3 页第 2 段",
            "原文摘录：流程记录",
            "无法判断原因：unresolved_relation",
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
        clauses = [{"id": "R1", "rule_to_check": long_rule}, {"id": "R2", "rule_to_check": "签字须完整"}, {"id": "R3", "rule_to_check": "未覆盖的要求也须列出"}]
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
        body = {
            "executor": "ppap-deep",
            "execution": "completed",
            "inputs": [
                {
                    "role": "target",
                    "file_version_id": "v1",
                    "original_filename": "plan.xlsx",
                    "rule_version_id": "rv1",
                    "rule": {"title": "客户规则", "source_file_version_id": "original-v", "body": {"rules": clauses}},
                },
                {"role": "reference", "file_version_id": "v2", "original_filename": "flow.pdf"},
                {"role": "reference", "file_version_id": "v3", "original_filename": "notes.docx"},
            ],
            "findings": [
                {
                    "status": "UNABLE",
                    "item": long_rule,
                    "classification": "频次",
                    "suggestions": "须补充图例",
                    "locator": {
                        "file_version_id": "v1",
                        "semantic": {"rule_version_id": "rv1", "clause_id": "R1", "rule_text": long_rule, "scope": {"total": 6, "checked": 4, "unknown": 2}, "sources": sources},
                    },
                },
                {
                    "status": "PASS",
                    "item": "签字须完整",
                    "classification": "签字",
                    "locator": {
                        "file_version_id": "v1",
                        "semantic": {"rule_version_id": "rv1", "clause_id": "R2", "rule_text": "签字须完整", "scope": {"total": 1, "checked": 1, "unknown": 0}, "sources": []},
                    },
                },
            ],
            "coverage": {
                "rule_items": [{"id": "R3", "file_version_id": "v1", "category": "特殊要求", "rule_to_check": "未覆盖的要求也须列出", "coverage": "unsupported", "reason": "manual-only"}],
                "unchecked": ["未覆盖的要求也须列出（未自动检查：无对应内置检查，需人工核对）", "flow.pdf（参考资料，不单独审核）"],
            },
        }
        blocks = report_blocks(body)
        contents = [part["text_run"]["content"] for block in blocks for value in block.values() if isinstance(value, dict) for part in value["elements"]]
        headings = [block["heading3"]["elements"][0]["text_run"]["content"] for block in blocks if "heading3" in block]
        self.assertEqual(headings, ["UNABLE · R1 · 频次", "PASS · R2 · 签字"])
        text = "\n".join(contents)
        for rule_text in (long_rule, "签字须完整", "未覆盖的要求也须列出"):
            self.assertEqual(text.count(rule_text), 1)
        for expected in (
            "R3 · 特殊要求：无对应内置检查，需人工核对",
            "工作表「控制计划」B2",
            "工作表「控制计划」C3:D8",
            "第 4 页第 7 段",
            "第 9 段",
            "第 2 个表格第 3 行第 4 列",
            "flow.pdf（参考）",
            "原文摘录：核对单元格",
            "未检查范围：flow.pdf（参考资料，不单独审核）",
        ):
            self.assertIn(expected, text)
        self.assertNotIn('"kind":', text)
