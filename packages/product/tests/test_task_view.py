import unittest
from copy import deepcopy

from product.store.task_view import has_execution_error, report_eligible, round_read_view, task_round_view


def frozen_round(execution="completed"):
    return {
        "id": "round-1",
        "execution": execution,
        "result_attempt": 1 if execution in {"completed", "failed"} else None,
        "failure": None,
        "coverage": {"rule_items": [{"file_version_id": "version-a", "id": "clause-a", "coverage": "unsupported", "reason": "没有对应的标准检查器"}]},
        "findings": [],
        "inputs": [
            {
                "role": "target",
                "file_version_id": "version-a",
                "rule": {"id": "rule-a", "title": "控制计划要求", "body": {"kind": "engine", "rules": [{"id": "clause-a", "category": "控制计划", "rule_to_check": "核对检验频次"}]}},
            }
        ],
    }


def task_input():
    return {"id": "task-1", "files": [{"file": {"id": "version-a"}, "selection": "selected"}]}


def verified_round(status="NG"):
    round = frozen_round()
    round.update(analysis_mode="deep", executor="ppap-deep", executor_revision="deep-v1", result_job_id="job-1")
    round["coverage"]["verification"] = {
        "method": "host_source_trace_v1",
        "round_id": "round-1",
        "job_id": "job-1",
        "attempt": 1,
        "executor_revision": "deep-v1",
        "semantic_engine_revision": "a" * 64,
    }
    round["coverage"]["rule_items"][0].update(
        coverage="executed",
        rule_version_id="rule-a",
        rule_item_id="clause-a",
        item_key="version-a:rule:clause-a",
        status=status,
        rule_to_check="核对检验频次",
        proof_kind="semantic",
    )
    round["findings"] = [
        {
            "item_key": "version-a:rule:clause-a",
            "item": "核对检验频次",
            "status": status,
            "review": None,
            "locator": {
                "file_version_id": "version-a",
                "rule_item_id": "clause-a",
                "semantic": {
                    "rule_version_id": "rule-a",
                    "clause_id": "clause-a",
                    "rule_text": "核对检验频次",
                    "scope": {"total": 2, "checked": 2, "unknown": 0},
                    "sources": [{"file_version_id": "version-a", "locator": {"kind": "xlsx_cell", "sheet": "参数", "cell": "B2"}, "excerpt": "每小时"}],
                },
            },
        }
    ]
    return round


class TaskRoundViewTests(unittest.TestCase):
    def test_checker_diagnostics_remain_when_a_source_is_outside_the_round(self):
        round = frozen_round()
        round["coverage"]["rule_items"][0].update(
            coverage="executed",
            verification_scope="checker_execution",
            status="UNABLE",
            rule_version_id="rule-a",
            rule_item_id="clause-a",
            item_key="version-a:rule:clause-a",
            rule_to_check="核对检验频次",
            checks_performed=1,
        )
        round["findings"] = [
            {
                "item_key": "version-a:rule:clause-a",
                "item": "核对检验频次",
                "status": "UNABLE",
                "suggestions": "未能读取",
                "review": None,
                "locator": {
                    "file_version_id": "version-a",
                    "rule_item_id": "clause-a",
                    "semantic": {
                        "rule_version_id": "rule-a",
                        "clause_id": "clause-a",
                        "rule_text": "核对检验频次",
                        "verification_scope": "checker_execution",
                        "checks_performed": 1,
                        "diagnostics": [{"code": "page_location_failed", "message": "第3页未能定位表格"}],
                        "sources": [{"file_version_id": "not-in-round", "locator": {"kind": "xlsx_cell", "sheet": "S", "cell": "A1"}, "excerpt": ""}],
                    },
                },
            }
        ]
        check = task_round_view(task_input(), round)["files"][0]["audit"]["checks"][0]
        self.assertIn("有来源不在本轮冻结文件中", check["scope_notice"])
        self.assertIn("page_location_failed：第3页未能定位表格", check["scope_notice"])
        self.assertIn("已执行 1 次断言", check["scope_notice"])
        self.assertFalse(check["evidence_verified"])
    def test_pdf_cell_is_preserved_in_task_and_historical_evidence(self):
        for page, row, column in ((1, 1, 1), (3, 12, 4)):
            with self.subTest(page=page, row=row, column=column):
                round = verified_round()
                locator = {"kind": "pdf_cell", "page": page, "row": row, "column": column}
                round["findings"][0]["locator"]["semantic"]["sources"][0]["locator"] = locator
                before = deepcopy(round)
                audit = task_round_view(task_input(), round)["files"][0]["audit"]
                check = audit["checks"][0]
                self.assertEqual(check["evidence"][0]["locator"], locator)
                self.assertIsNone(check["evidence"][0]["location_gap"])
                self.assertEqual(check["evidence"][0]["rendition"], "original")
                self.assertFalse(check["evidence_verified"])
                self.assertEqual((check["finding"]["status"], audit["conclusion"]), ("NG", "NG"))
                self.assertEqual(round_read_view(round, {"version-a": {"id": "version-a"}})["files"][0]["audit"], audit)
                self.assertEqual(round, before)

    def test_checker_pdf_page_stays_visible_without_trace_verification(self):
        round = verified_round("UNABLE")
        round["coverage"].pop("verification")
        round["coverage"]["rule_items"][0].pop("proof_kind", None)
        locator = {"kind": "pdf_page", "page": 3, "region": [0, 12, 400, 700]}
        round["findings"][0]["status"] = "UNABLE"
        round["coverage"]["rule_items"][0]["status"] = "UNABLE"
        round["findings"][0]["locator"]["semantic"]["sources"] = [
            {"file_version_id": "version-a", "kind": "structure", "locator": locator}
        ]
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        check = audit["checks"][0]
        self.assertEqual(check["evidence"][0]["locator"], locator)
        self.assertIsNone(check["evidence"][0]["location_gap"])
        self.assertFalse(check["evidence_verified"])
        self.assertIsNone(check["verification_scope"])
        partial = {"kind": "pdf_page", "page": 4, "region": [None, 10.5, None, 80.25]}
        round["findings"][0]["locator"]["semantic"]["sources"][0]["locator"] = partial
        partial_check = task_round_view(task_input(), round)["files"][0]["audit"]["checks"][0]
        self.assertEqual(partial_check["evidence"][0]["locator"], partial)
        self.assertIsNone(partial_check["evidence"][0]["location_gap"])
        self.assertEqual(check["finding"]["status"], "UNABLE")


    def test_checker_cells_from_both_files_stay_visible_without_trace_verification(self):
        round = verified_round("NG")
        round["coverage"].pop("verification")
        round["coverage"]["rule_items"][0].pop("proof_kind", None)
        round["coverage"]["rule_items"][0]["verification_scope"] = "checker_execution"
        round["inputs"].append({"role": "reference", "file_version_id": "related-version", "rule": None})
        round["findings"][0]["locator"]["semantic"].pop("scope", None)
        round["findings"][0]["locator"]["semantic"]["verification_scope"] = "checker_execution"
        round["findings"][0]["locator"]["semantic"]["sources"] = [
            {"file_version_id": "version-a", "kind": "structure", "locator": {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}},
            {"file_version_id": "related-version", "kind": "structure", "locator": {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2"}},
        ]
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        check = audit["checks"][0]
        self.assertEqual(
            [(item["file_version_id"], item["locator"], item["location_gap"]) for item in check["evidence"]],
            [
                ("version-a", {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A2"}, None),
                ("related-version", {"kind": "xlsx_cell", "sheet": "特殊特性清单", "cell": "B2"}, None),
            ],
        )
        self.assertFalse(check["evidence_verified"])
        self.assertEqual(check["verification_scope"], "checker_execution")
        self.assertNotIn("verification", round["coverage"])
        self.assertEqual(check["finding"]["status"], "NG")

    def test_a_finished_checker_scan_keeps_its_row_counts_without_trace_verification(self):
        round = verified_round("PASS")
        round["coverage"].pop("verification")
        round["coverage"]["rule_items"][0]["verification_scope"] = "checker_execution"
        semantic = round["findings"][0]["locator"]["semantic"]
        semantic["verification_scope"] = "checker_execution"
        semantic["scope"] = {"total": 2, "checked": 2, "unknown": 0}
        semantic["checks_performed"] = 4
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        check = audit["checks"][0]
        self.assertEqual(check["scope"], {"total": 2, "checked": 2, "unknown": 0})
        self.assertFalse(check["evidence_verified"])
        self.assertIn("已执行 4 次断言", check["scope_notice"])
        self.assertNotIn("已检查 4/4", check["scope_notice"] or "")

    def test_writeback_rejects_an_unresolved_cell_and_keeps_a_pdf_page(self):
        from product.feishu_writeback import report_blocks, writeback_state

        def body(locator: dict) -> dict:
            return {
                "analysis_mode": "deep",
                "execution": "completed",
                "inputs": [
                    {
                        "role": "target",
                        "file_version_id": "target-v",
                        "original_filename": "flow.pdf",
                        "rule_version_id": "rule-v",
                        "rule": {"body": {"rules": [{"id": "R1", "rule_to_check": "页码保留"}]}},
                    }
                ],
                "findings": [
                    {
                        "item": "页码保留",
                        "status": "UNABLE",
                        "suggestions": "已看到页面",
                        "locator": {
                            "file_version_id": "target-v",
                            "semantic": {
                                "rule_version_id": "rule-v",
                                "clause_id": "R1",
                                "rule_text": "页码保留",
                                "scope": {"total": 1, "checked": 1, "unknown": 0},
                                "sources": [
                                    {
                                        "file_version_id": "target-v",
                                        "filename": "flow.pdf",
                                        "role": "target",
                                        "locator": locator,
                                        "excerpt": "流程",
                                    }
                                ],
                            },
                        },
                    }
                ],
            }

        ready = body({"kind": "pdf_page", "page": 3, "region": [0, 12, 400, 700]})
        report_text = "\n".join(
            element["text_run"]["content"]
            for block in report_blocks(ready)
            for element in (block.get("text") or block.get("bullet") or {}).get("elements", [])
        )
        self.assertEqual(writeback_state(ready), "ready")
        self.assertIn("第 3 页", report_text)
        self.assertNotIn("审核结果：PASS", report_text)
        self.assertEqual(writeback_state(body({"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A0"})), "round_incomplete")

    def test_unresolved_coordinates_are_location_gaps_without_changing_the_verdict(self):
        invalid = (
            {"kind": "xlsx_cell", "sheet": "过程流程图", "cell": "A0"},
            {"kind": "xlsx_range", "sheet": "过程", "start": "a1", "end": "B2"},
            {"kind": "docx_paragraph", "paragraph": 0},
            {"kind": "pdf_block", "page": 1, "block": 0},
            {"kind": "docx_cell", "table": 0, "row": 1, "column": 1},
            {"kind": "pdf_page", "page": 2, "region": [True, 0, 1, 1]},
        )
        for candidate in invalid:
            with self.subTest(locator=candidate):
                round = verified_round("PASS")
                round["findings"][0]["locator"]["semantic"]["sources"][0]["locator"] = candidate
                audit = task_round_view(task_input(), round)["files"][0]["audit"]
                check = audit["checks"][0]
                self.assertIsNone(check["evidence"][0]["locator"])
                self.assertTrue(check["evidence"][0]["location_gap"])
                self.assertEqual(check["finding"]["status"], "PASS")

    def test_invalid_pdf_cell_is_a_location_gap_without_changing_the_verdict(self):
        locator = {"kind": "pdf_cell", "page": 1, "row": 1, "column": 1}
        invalid = [{**locator, field: value} for field in ("page", "row", "column") for value in (None, True, "1", 1.5, 0, -1)]
        invalid.extend({key: value for key, value in locator.items() if key != field} for field in ("page", "row", "column"))
        invalid.extend(({**locator, "table": 1}, {**locator, "bbox": [0, 0, 1, 1]}))
        for candidate in invalid:
            with self.subTest(locator=candidate):
                round = verified_round("PASS")
                round["findings"][0]["locator"]["semantic"]["sources"][0]["locator"] = candidate
                audit = task_round_view(task_input(), round)["files"][0]["audit"]
                check = audit["checks"][0]
                self.assertIsNone(check["evidence"][0]["locator"])
                self.assertTrue(check["evidence"][0]["location_gap"])
                self.assertFalse(check["evidence_verified"])
                self.assertEqual((check["finding"]["status"], audit["conclusion"], audit["counts"]["pass_count"]), ("PASS", "PASS", 1))
                self.assertTrue(report_eligible(audit["checks"], False, audit["unmapped_findings"]))

    def test_execution_fault_projection_keeps_round_state_and_valid_verdicts_separate(self):
        for coverage in ("unsupported", "executed", "execution_error"):
            for status in (None, "PASS", "NG", "NA", "UNABLE", "ERR"):
                with self.subTest(coverage=coverage, status=status):
                    round = frozen_round()
                    row = round["coverage"]["rule_items"][0]
                    row.update(coverage=coverage, item_key="version-a:rule:clause-a")
                    if status:
                        round["findings"] = [{"item_key": row["item_key"], "status": status, "locator": {"file_version_id": "version-a", "rule_item_id": "clause-a"}, "review": None}]
                    before = deepcopy(round)
                    view = task_round_view(task_input(), round)
                    audit = view["files"][0]["audit"]
                    failed = status == "ERR" or status is None and coverage == "execution_error"
                    self.assertEqual(view["execution"], "completed")
                    self.assertEqual(view["has_execution_error"], failed)
                    self.assertEqual(audit["execution"], "review_failed" if failed else "completed")
                    self.assertEqual(audit["checks"][0]["execution"], "failed" if failed else "completed")
                    self.assertEqual(audit["checks"][0]["finding"]["status"] if status else audit["checks"][0]["finding"], status)
                    self.assertEqual(audit["counts"]["error_count"], int(failed))
                    self.assertEqual(round, before)
        self.assertTrue(has_execution_error("failed", {}, []))
        self.assertFalse(has_execution_error("running", {"rule_items": [{"coverage": "execution_error"}]}, []))
        round = verified_round("PASS")
        round["coverage"]["rule_items"][0].update(coverage="execution_error", item_key="legacy-coverage-key")
        view = task_round_view(task_input(), round)
        self.assertFalse(view["has_execution_error"])
        self.assertEqual(view["files"][0]["audit"]["checks"][0]["finding"]["status"], "PASS")
        round["execution"] = "running"
        round["findings"] = []
        running = task_round_view(task_input(), round)
        self.assertFalse(running["has_execution_error"])
        self.assertEqual(running["files"][0]["audit"]["checks"][0]["execution"], "running")
        for duplicate in ("coverage", "findings"):
            with self.subTest(duplicate=duplicate):
                round = verified_round("PASS")
                round["coverage"]["rule_items"][0]["coverage"] = "execution_error"
                rows = round["coverage"]["rule_items"] if duplicate == "coverage" else round["findings"]
                rows.append(deepcopy(rows[0]))
                ambiguous = task_round_view(task_input(), round)
                self.assertFalse(ambiguous["has_execution_error"])
                self.assertEqual(ambiguous["files"][0]["audit"]["execution"], "completed")
                self.assertEqual(ambiguous["files"][0]["audit"]["checks"][0]["coverage"], "unsupported")

    def test_frozen_reference_does_not_prevent_completion_or_create_an_audit(self):
        round = frozen_round()
        round["inputs"].append({"role": "reference", "file_version_id": "reference-v1", "rule": None})
        task = task_input()
        task["files"].append({"file": {"id": "reference-v1"}, "selection": "selected", "match": {"state": "reference", "rule": None}})
        view = task_round_view(task, round)
        self.assertEqual(view["state"], "completed")
        self.assertEqual(view["progress"], {"completed": 1, "total": 1})
        self.assertEqual(view["files"][1]["selection"], "selected")
        self.assertIsNone(view["files"][1]["audit"])

    def test_missing_optional_evidence_preserves_actual_results_counts_and_report_eligibility(self):
        for status in ("PASS", "NG"):
            with self.subTest(status=status):
                round = verified_round(status)
                round["analysis_mode"] = "standard"
                round["coverage"].pop("verification")
                round["findings"][0]["locator"].pop("semantic")
                before = deepcopy(round)
                view = task_round_view(task_input(), round)
                audit = view["files"][0]["audit"]
                self.assertEqual(view["state"], "completed")
                self.assertEqual(audit["conclusion"], status)
                self.assertEqual(audit["counts"]["pass_count" if status == "PASS" else "ng_count"], 1)
                self.assertEqual(audit["counts"]["unknown_count"], 0)
                self.assertFalse(audit["checks"][0]["evidence_verified"])
                self.assertTrue(audit["checks"][0]["scope_notice"])
                self.assertTrue(report_eligible(audit["checks"], False, audit["unmapped_findings"]))
                self.assertEqual(round, before)

    def test_only_not_applicable_results_do_not_become_a_file_pass(self):
        round = verified_round("NA")
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(audit["checks"][0]["finding"]["status"], "NA")
        self.assertEqual(audit["conclusion"], "UNKNOWN")
        self.assertEqual(audit["counts"]["pass_count"], 0)
        self.assertEqual(audit["counts"]["ng_count"], 0)
        self.assertEqual(audit["counts"]["na_count"], 1)
        self.assertIn("不适用 1 项", audit["summary"]["text"])
        self.assertNotEqual(audit["summary"]["conclusion"], "PASS")

    def test_complete_verified_results_have_a_shared_summary_without_invented_recommendations(self):
        round = verified_round()
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(
            audit["summary"],
            {"conclusion": "NG", "text": "已审核 1 项要求：满足 0 项，不符合 1 项，不适用 0 项。", "corrective_actions": [], "extended_suggestions": []},
        )
        self.assertEqual(round_read_view(round, {"version-a": {"id": "version-a"}})["files"][0]["audit"]["summary"], audit["summary"])
        round["findings"][0]["locator"]["semantic"]["scope"]["unknown"] = 1
        incomplete = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(incomplete["conclusion"], "NG")
        self.assertEqual(incomplete["summary"], audit["summary"])
        self.assertTrue(incomplete["checks"][0]["scope_notice"])

    def test_host_validated_source_and_trace_reach_task_and_historical_results(self):
        round = verified_round()
        before = deepcopy(round)
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["checks"][0]["evidence"][0]["excerpt"], "每小时")
        self.assertEqual(audit["counts"], {"pass_count": 0, "ng_count": 1, "na_count": 0, "unknown_count": 0, "not_covered_count": 0, "error_count": 0})
        self.assertEqual(audit["conclusion"], "NG")
        self.assertEqual(round_read_view(round, {"version-a": {"id": "version-a"}})["files"][0]["audit"], audit)
        self.assertEqual(round, before)

    def test_verification_cannot_be_borrowed_from_another_attempt_rule_or_execution(self):
        paths = [
            (("coverage", "verification"), None),
            (("coverage", "verification", "method"), "another_validator"),
            (("coverage", "verification", "round_id"), "another-round"),
            (("coverage", "verification", "job_id"), "another-job"),
            (("coverage", "verification", "attempt"), 2),
            (("coverage", "verification", "attempt"), True),
            (("coverage", "verification", "executor_revision"), "another-revision"),
            (("coverage", "verification", "semantic_engine_revision"), "not-a-revision"),
            (("analysis_mode",), "standard"),
            (("coverage", "rule_items", 0, "rule_version_id"), "another-rule"),
            (("coverage", "rule_items", 0, "rule_item_id"), "another-clause"),
            (("coverage", "rule_items", 0, "item_key"), "another-result"),
            (("coverage", "rule_items", 0, "status"), "PASS"),
            (("coverage", "rule_items", 0, "rule_to_check"), "another rule text"),
            (("coverage", "rule_items", 0, "proof_kind"), "model_says_verified"),
            (("findings", 0, "item"), "another rule text"),
            (("findings", 0, "locator", "file_version_id"), None),
            (("findings", 0, "locator", "rule_item_id"), None),
            (("findings", 0, "locator", "semantic", "rule_text"), "another rule text"),
            (("findings", 0, "locator", "semantic", "scope", "unknown"), True),
            (("findings", 0, "locator", "semantic", "sources", 0, "file_version_id"), "outside-round"),
            (("findings", 0, "locator", "semantic", "sources", 0, "locator"), None),
        ]
        for path, value in paths:
            with self.subTest(path=path, value=value):
                round = verified_round()
                container = round
                for key in path[:-1]:
                    container = container[key]
                container[path[-1]] = value
                audit = task_round_view(task_input(), round)["files"][0]["audit"]
                self.assertFalse(audit["checks"][0]["evidence_verified"])
                check = audit["checks"][0]
                if check["finding"] is not None:
                    self.assertEqual((check["finding"]["status"], audit["conclusion"]), ("NG", "NG"))
                else:
                    self.assertEqual(audit["conclusion"], "UNKNOWN")

    def test_scope_gap_is_informational_but_a_missing_check_still_prevents_a_complete_report(self):
        round = verified_round()
        round["findings"][0]["locator"]["semantic"]["scope"] = {"total": 3, "checked": 2, "unknown": 1}
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["ng_count"]), ("NG", 1))
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["report"]["state"], "not_eligible")
        self.assertEqual(audit["report"]["reason"], "正式报告暂不可用")
        self.assertTrue(audit["checks"][0]["scope_notice"])
        self.assertTrue(report_eligible(audit["checks"], False, audit["unmapped_findings"]))
        round["inputs"][0]["rule"]["body"]["rules"].append({"id": "clause-b", "category": "其他", "rule_to_check": "另一项要求"})
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["not_covered_count"]), ("NG", 1))
        self.assertEqual(audit["report"]["state"], "not_eligible")
        self.assertFalse(report_eligible(audit["checks"], False, audit["unmapped_findings"]))

    def test_optional_scope_gap_does_not_change_pass_and_human_review_does_not_replace_unable(self):
        round = verified_round("PASS")
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["pass_count"]), ("PASS", 1))
        self.assertEqual(audit["report"]["reason"], "正式报告暂不可用")
        round["findings"][0]["locator"]["semantic"]["scope"]["unknown"] = 1
        round["findings"][0]["review"] = {"judgment": "manual-satisfied", "rationale": "人工意见", "next_step": "", "version": 1, "citations": []}
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["pass_count"], audit["counts"]["unknown_count"]), ("PASS", 1, 0))
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        self.assertTrue(report_eligible(audit["checks"], False, audit["unmapped_findings"]))
        round["findings"][0]["status"] = "UNABLE"
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["unknown_count"]), ("UNKNOWN", 1))
        self.assertEqual(audit["report"]["state"], "not_eligible")

    def test_duplicate_validation_coverage_or_findings_are_not_verified(self):
        for field in ("coverage", "findings"):
            round = verified_round()
            rows = round["coverage"]["rule_items"] if field == "coverage" else round["findings"]
            rows.append(deepcopy(rows[0]))
            audit = task_round_view(task_input(), round)["files"][0]["audit"]
            self.assertFalse(audit["checks"][0]["evidence_verified"])
            self.assertEqual((audit["conclusion"], audit["report"]["state"]), ("UNKNOWN", "not_eligible"))

    def test_a_known_empty_cell_can_be_validated_ng_evidence(self):
        round = verified_round()
        round["findings"][0]["locator"]["semantic"]["sources"][0]["excerpt"] = ""
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["conclusion"], "NG")

    def test_a_global_execution_error_fails_targets_without_assigning_an_unrelated_error(self):
        round = frozen_round()
        round["inputs"].append({**round["inputs"][0], "file_version_id": "version-b"})
        task = task_input()
        task["files"].append({"file": {"id": "version-b"}, "selection": "selected"})
        round["findings"] = [{"item_key": "unassigned", "status": "ERR", "locator": None, "review": None}]
        self.assertTrue(all(row["audit"]["execution"] == "completed" for row in task_round_view(task, round)["files"]))
        round["findings"][0]["item_key"] = "execution"
        before = deepcopy(round)
        view = task_round_view(task, round)
        self.assertEqual([row["audit"]["execution"] for row in view["files"]], ["review_failed", "review_failed"])
        self.assertTrue(all(row["audit"]["checks"][0]["execution"] == "failed" for row in view["files"]))
        self.assertEqual(view["progress"], {"completed": 2, "total": 2})
        self.assertEqual(round, before)

    def test_a_clause_error_marks_only_its_file_abnormal_and_preserves_completed_checks(self):
        round = frozen_round()
        round["inputs"][0]["rule"]["body"]["rules"].append({"id": "clause-b", "category": "基本信息", "rule_to_check": "核对零件号"})
        round["findings"] = [
            {"item_key": "version-a:rule:clause-a", "status": "ERR", "locator": {"file_version_id": "version-a", "rule_item_id": "clause-a"}, "review": None},
            {"item_key": "version-a:rule:clause-b", "status": "PASS", "locator": {"file_version_id": "version-a", "rule_item_id": "clause-b"}, "review": None},
        ]
        before = deepcopy(round)
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(audit["execution"], "review_failed")
        self.assertEqual([check["execution"] for check in audit["checks"]], ["failed", "completed"])
        self.assertEqual([check["finding"]["status"] for check in audit["checks"]], ["ERR", "PASS"])
        self.assertEqual((audit["counts"]["error_count"], audit["counts"]["pass_count"], audit["counts"]["unknown_count"]), (1, 1, 0))
        self.assertEqual(round, before)

    def test_a_file_execution_failure_does_not_mark_the_other_frozen_file_failed_or_finished_without_results(self):
        round = frozen_round()
        round["inputs"].append({**round["inputs"][0], "file_version_id": "version-b"})
        round["findings"] = [
            {"item_key": "version-a:execution", "status": "ERR", "suggestions": "无法读取本轮冻结原件。", "locator": {"file_version_id": "version-a"}, "review": None},
            {"item_key": "healthy", "status": "NG", "locator": {"file_version_id": "version-b", "rule_item_id": "clause-a"}, "review": None},
        ]
        task = task_input()
        task["files"].append({"file": {"id": "version-b"}, "selection": "selected"})
        view = task_round_view(task, round)
        broken, healthy = [row["audit"] for row in view["files"]]
        self.assertEqual(broken["execution"], "review_failed")
        self.assertEqual(broken["checks"][0]["execution"], "failed")
        self.assertEqual((broken["counts"]["error_count"], broken["failure"]["retryable"]), (1, False))
        self.assertEqual((healthy["execution"], healthy["checks"][0]["finding"]["status"]), ("completed", "NG"))
        self.assertEqual(view["progress"], {"completed": 2, "total": 2})
        self.assertEqual((broken["report"]["state"], healthy["report"]["state"]), ("not_eligible", "not_eligible"))

    def test_review_key_requires_an_existing_finding_or_nonexecuted_coverage_record(self):
        round = frozen_round()
        round["coverage"] = {}
        check = task_round_view(task_input(), round)["files"][0]["audit"]["checks"][0]
        self.assertNotIn("review_item_key", check)
        round["coverage"] = {"rule_items": [{"file_version_id": "version-a", "id": "clause-a", "coverage": "executed"}]}
        self.assertNotIn("review_item_key", task_round_view(task_input(), round)["files"][0]["audit"]["checks"][0])
        round["coverage"]["rule_items"][0]["coverage"] = "unsupported"
        self.assertEqual(task_round_view(task_input(), round)["files"][0]["audit"]["checks"][0]["review_item_key"], "coverage:version-a:clause-a")
        self.assertNotIn("review_item_key", task_round_view(task_input(), frozen_round("running"))["files"][0]["audit"]["checks"][0])

    def test_conflicting_historical_file_identities_remain_unassigned(self):
        round = frozen_round()
        round["inputs"].append({**round["inputs"][0], "file_version_id": "version-b"})
        finding = {"item_key": "version-a:engine:1", "status": "ERR", "locator": {"file_version_id": "version-b"}, "review": None}
        round["findings"] = [finding]
        view = round_read_view(round, {key: {"id": key} for key in ("version-a", "version-b")})
        self.assertEqual(view["unassigned_findings"], [finding])
        self.assertTrue(all(not row["audit"]["unmapped_findings"] for row in view["files"]))
        self.assertTrue(all(row["audit"]["execution"] == "completed" for row in view["files"]))

    def test_unmapped_engine_errors_mark_the_file_abnormal_without_inventing_clause_results(self):
        round = frozen_round()
        finding = {"item_key": "version-a:engine:1", "status": "ERR", "locator": {"file_version_id": "version-a"}, "review": None}
        round["findings"] = [finding]
        before = deepcopy(round)
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(audit["execution"], "review_failed")
        self.assertEqual(audit["unmapped_findings"], [finding])
        self.assertIsNone(audit["checks"][0]["finding"])
        self.assertEqual(audit["counts"]["not_covered_count"], 1)
        self.assertEqual(audit["report"]["state"], "not_eligible")
        self.assertEqual(round, before)

    def test_task_and_round_readers_share_the_same_frozen_file_projection(self):
        round = frozen_round()
        direct = round_read_view(round, {"version-a": {"id": "version-a"}})
        task = task_round_view(task_input(), round)
        self.assertEqual(direct["files"][0]["audit"], task["files"][0]["audit"])

    def test_round_reader_preserves_unmapped_engine_presence_and_unassigned_records_without_creating_rule_items(self):
        round = frozen_round()
        presence = {"id": "presence-rule", "title": "文件存在", "body": {"kind": "presence"}}
        round["inputs"].append({"role": "target", "file_version_id": "version-b", "rule": presence})
        review = {"judgment": "disagree", "rationale": "旧记录人工意见", "next_step": "", "version": 1, "citations": []}
        round["findings"] = [
            {"item_key": "version-a:engine:1", "status": "PASS", "locator": {"file_version_id": "version-a"}, "review": review},
            {"item_key": "target:version-b", "status": "NG", "locator": {"file_version_id": "version-b"}, "review": None},
            {"item_key": "execution", "status": "ERR", "locator": None, "review": None},
        ]
        versions = {key: {"id": key, "original_filename": key + ".xlsx"} for key in ("version-a", "version-b")}
        before = deepcopy(round)
        view = round_read_view(round, versions)
        self.assertEqual([row["file"]["id"] for row in view["files"]], ["version-a", "version-b"])
        first, second = view["files"]
        self.assertEqual(first["audit"]["unmapped_findings"], [round["findings"][0]])
        self.assertEqual(second["audit"]["unmapped_findings"], [round["findings"][1]])
        self.assertEqual(view["unassigned_findings"], [round["findings"][2]])
        self.assertEqual(first["audit"]["checks"][0]["review"], None)
        self.assertEqual((first["audit"]["conclusion"], first["audit"]["report"]["state"]), ("UNKNOWN", "not_eligible"))
        self.assertEqual(second["audit"]["checks"], [])
        self.assertEqual(round, before)

    def test_finished_unsupported_check_ends_task_without_fabricating_pass_or_report(self):
        view = task_round_view(task_input(), frozen_round())
        self.assertEqual((view["state"], view["completion_reason"], view["progress"]), ("completed", "processed", {"completed": 1, "total": 1}))
        audit = view["files"][0]["audit"]
        self.assertEqual((audit["execution"], audit["progress"], audit["conclusion"]), ("completed", {"completed": 1, "total": 1}, "UNKNOWN"))
        self.assertEqual(audit["counts"], {"pass_count": 0, "ng_count": 0, "na_count": 0, "unknown_count": 0, "not_covered_count": 1, "error_count": 0})
        self.assertEqual((audit["checks"][0]["coverage"], audit["checks"][0]["finding"]), ("unsupported", None))
        self.assertFalse(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["report"]["state"], "not_eligible")

    def test_findings_keep_per_file_identity_and_human_review_without_treating_legacy_locator_as_verified_evidence(self):
        task, round = task_input(), frozen_round()
        second = deepcopy(round["inputs"][0])
        second.update(file_version_id="version-b", rule={**second["rule"], "id": "rule-b"})
        round["inputs"].append(second)
        task["files"].append({"file": {"id": "version-b"}, "selection": "selected"})
        saved_review = {"judgment": "disagree", "rationale": "人工核对为不符合", "next_step": "补正", "version": 2, "citations": []}
        round["coverage"]["rule_items"] = [{"file_version_id": "version-b", "id": "clause-a", "coverage": "mapped", "item_key": "original-b"}]
        round["findings"] = [
            {"item_key": "original-b", "status": "NG", "locator": None, "review": None},
            {"item_key": "original-a", "status": "PASS", "locator": {"file_version_id": "version-a", "rule_item_id": "clause-a", "locator": "Sheet1!A2"}, "review": saved_review},
        ]
        before = deepcopy(round)
        result = task_round_view(task, round)
        first, second = [row["audit"] for row in result["files"]]
        self.assertEqual([first["checks"][0]["review_item_key"], second["checks"][0]["review_item_key"]], ["original-a", "original-b"])
        self.assertEqual([first["checks"][0]["finding"]["status"], second["checks"][0]["finding"]["status"]], ["PASS", "NG"])
        self.assertEqual(first["checks"][0]["finding"]["item_key"], "clause-a")
        self.assertEqual(first["checks"][0]["review"], saved_review)
        self.assertEqual((first["conclusion"], second["conclusion"]), ("PASS", "NG"))
        self.assertEqual((first["counts"]["pass_count"], first["counts"]["unknown_count"]), (1, 0))
        self.assertEqual((second["counts"]["ng_count"], second["counts"]["unknown_count"]), (1, 0))
        self.assertEqual(first["checks"][0]["evidence"][0]["excerpt"], "")
        self.assertIn("Sheet1!A2", first["checks"][0]["evidence"][0]["location_gap"])
        self.assertFalse(first["checks"][0]["evidence_verified"])
        self.assertNotEqual(first["checks"][0]["scope_notice"], round["coverage"].get("rule_items", [{}])[0].get("reason"))
        self.assertEqual(round, before)

    def test_running_and_failed_execution_do_not_look_queued_or_keep_task_open(self):
        running = task_round_view(task_input(), frozen_round("running"))
        self.assertEqual((running["state"], running["files"][0]["audit"]["execution"], running["files"][0]["audit"]["checks"][0]["execution"]), ("reviewing", "running", "running"))
        self.assertEqual(running["progress"], {"completed": 0, "total": 1})
        failed_round = frozen_round("failed")
        failed_round["failure"] = {"code": "missing_file", "message": "无法读取冻结文件", "retryable": False}
        failed = task_round_view(task_input(), failed_round)
        audit = failed["files"][0]["audit"]
        self.assertEqual((failed["state"], failed["completion_reason"], failed["progress"]), ("completed", "processed", {"completed": 1, "total": 1}))
        self.assertEqual((audit["execution"], audit["checks"][0]["execution"], audit["counts"]["error_count"]), ("review_failed", "failed", 1))
        self.assertEqual(audit["failure"], failed_round["failure"])
        self.assertEqual(audit["report"]["state"], "not_eligible")

    def test_coverage_only_human_review_stays_separate_from_machine_results(self):
        round = frozen_round()
        review = {
            "judgment": "manual-satisfied",
            "rationale": "人工按原件核对",
            "next_step": "",
            "version": 1,
            "citations": [{"file_version_id": "version-a", "location": "Sheet1 A1", "excerpt": "每班"}],
        }
        round["coverage"]["rule_items"][0]["review"] = review
        before = deepcopy(round)
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        check = audit["checks"][0]
        self.assertEqual((check["review_item_key"], check["review"]), ("coverage:version-a:clause-a", review))
        self.assertIsNone(check["finding"])
        self.assertEqual((check["coverage"], check["evidence"], check["evidence_verified"]), ("unsupported", [], False))
        self.assertEqual((audit["counts"]["pass_count"], audit["counts"]["not_covered_count"], audit["conclusion"], audit["report"]["state"]), (0, 1, "UNKNOWN", "not_eligible"))
        self.assertEqual(round, before)

    def test_semantic_evidence_keeps_frozen_source_locations_without_claiming_read_time_verification(self):
        round = frozen_round()
        round["inputs"].append({"role": "reference", "file_version_id": "version-reference", "rule": None})
        round["coverage"]["rule_items"][0]["coverage"] = "executed"
        round["findings"] = [
            {
                "item_key": "version-a:rule:clause-a",
                "status": "NG",
                "review": None,
                "locator": {
                    "file_version_id": "version-a",
                    "rule_item_id": "clause-a",
                    "semantic": {
                        "rule_version_id": "rule-a",
                        "clause_id": "clause-a",
                        "scope": {"total": 2, "checked": 2, "unknown": 0},
                        "sources": [
                            {"file_version_id": "version-reference", "locator": {"kind": "xlsx_cell", "sheet": "参数", "cell": "B2"}, "excerpt": "每小时"},
                            {"file_version_id": "not-in-round", "locator": {"kind": "xlsx_cell", "sheet": "参数", "cell": "C2"}, "excerpt": "不得进入本轮的内容"},
                        ],
                    },
                },
            }
        ]
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        check = audit["checks"][0]
        self.assertEqual(check["scope"], {"total": 2, "checked": 2, "unknown": 0})
        self.assertEqual(
            check["evidence"],
            [
                {
                    "file_version_id": "version-reference",
                    "locator": {"kind": "xlsx_cell", "sheet": "参数", "cell": "B2"},
                    "excerpt": "每小时",
                    "location_gap": None,
                    "rendition": "original",
                    "derived_file_version_id": None,
                }
            ],
        )
        self.assertFalse(check["evidence_verified"])
        self.assertEqual((audit["counts"]["ng_count"], audit["counts"]["unknown_count"], audit["conclusion"], audit["report"]["state"]), (1, 0, "NG", "not_eligible"))

    def test_task_progress_does_not_claim_completion_for_a_selected_file_without_a_frozen_result(self):
        task = task_input()
        task["files"].extend([{"file": {"id": "missing-version"}, "selection": "selected"}, {"file": {"id": "removed-version"}, "selection": "removed", "removal_reason": "未匹配"}])
        view = task_round_view(task, frozen_round())
        self.assertEqual((view["state"], view["completion_reason"], view["progress"]), ("reviewing", None, {"completed": 1, "total": 2}))
        self.assertEqual(view["files"][2]["removal_reason"], "未匹配")
        self.assertIsNone(view["files"][1].get("audit"))
