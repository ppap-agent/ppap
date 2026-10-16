import unittest
from copy import deepcopy

from ppap_product_store.task_view import round_read_view, task_round_view


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
    def test_complete_verified_results_have_a_shared_summary_without_invented_recommendations(self):
        round = verified_round()
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(
            audit["summary"],
            {"conclusion": "NG", "text": "已核验 1 项要求：满足 0 项，不符合 1 项，不适用 0 项。", "corrective_actions": [], "extended_suggestions": []},
        )
        self.assertEqual(round_read_view(round, {"version-a": {"id": "version-a"}})["files"][0]["audit"]["summary"], audit["summary"])
        round["findings"][0]["locator"]["semantic"]["scope"]["unknown"] = 1
        incomplete = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual(incomplete["conclusion"], "NG")
        self.assertIsNone(incomplete["summary"])

    def test_host_validated_source_and_trace_reach_task_and_historical_results(self):
        round = verified_round()
        before = deepcopy(round)
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertTrue(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["checks"][0]["evidence"][0]["excerpt"], "每小时")
        self.assertEqual(audit["counts"], {"pass_count": 0, "ng_count": 1, "unknown_count": 0, "not_covered_count": 0, "error_count": 0})
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
                self.assertEqual((audit["conclusion"], audit["report"]["state"]), ("UNKNOWN", "not_eligible"))

    def test_valid_ng_does_not_hide_unknown_scope_or_make_an_incomplete_file_reportable(self):
        round = verified_round()
        round["findings"][0]["locator"]["semantic"]["scope"] = {"total": 3, "checked": 2, "unknown": 1}
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["ng_count"]), ("NG", 1))
        self.assertTrue(audit["checks"][0]["evidence_verified"])
        self.assertEqual(audit["report"]["state"], "not_eligible")
        self.assertEqual(audit["report"]["reason"], "审核依据尚未完整核验")
        round["inputs"][0]["rule"]["body"]["rules"].append({"id": "clause-b", "category": "其他", "rule_to_check": "另一项要求"})
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["not_covered_count"]), ("NG", 1))
        self.assertEqual(audit["report"]["state"], "not_eligible")

    def test_only_complete_verified_pass_is_pass_and_a_human_review_does_not_fill_the_gap(self):
        round = verified_round("PASS")
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["pass_count"]), ("PASS", 1))
        self.assertEqual(audit["report"]["reason"], "正式报告暂不可用")
        round["findings"][0]["locator"]["semantic"]["scope"]["unknown"] = 1
        round["findings"][0]["review"] = {"judgment": "manual-satisfied", "rationale": "人工意见", "next_step": "", "version": 1, "citations": []}
        audit = task_round_view(task_input(), round)["files"][0]["audit"]
        self.assertEqual((audit["conclusion"], audit["counts"]["unknown_count"]), ("UNKNOWN", 1))
        self.assertFalse(audit["checks"][0]["evidence_verified"])
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
        self.assertTrue(audit["checks"][0]["evidence_verified"])
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
        self.assertEqual((audit["counts"]["error_count"], audit["counts"]["unknown_count"]), (1, 1))
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
        self.assertEqual(audit["counts"], {"pass_count": 0, "ng_count": 0, "unknown_count": 0, "not_covered_count": 1, "error_count": 0})
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
        self.assertEqual((first["conclusion"], second["conclusion"]), ("UNKNOWN", "UNKNOWN"))
        self.assertEqual(first["counts"]["unknown_count"], 1)
        self.assertEqual(second["counts"]["unknown_count"], 1)
        self.assertEqual(first["checks"][0]["evidence"][0]["excerpt"], "")
        self.assertIn("Sheet1!A2", first["checks"][0]["evidence"][0]["location_gap"])
        self.assertFalse(first["checks"][0]["evidence_verified"])
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
        self.assertEqual((audit["counts"]["ng_count"], audit["counts"]["unknown_count"], audit["conclusion"], audit["report"]["state"]), (0, 1, "UNKNOWN", "not_eligible"))

    def test_task_progress_does_not_claim_completion_for_a_selected_file_without_a_frozen_result(self):
        task = task_input()
        task["files"].extend([{"file": {"id": "missing-version"}, "selection": "selected"}, {"file": {"id": "removed-version"}, "selection": "removed", "removal_reason": "未匹配"}])
        view = task_round_view(task, frozen_round())
        self.assertEqual((view["state"], view["completion_reason"], view["progress"]), ("reviewing", None, {"completed": 1, "total": 2}))
        self.assertEqual(view["files"][2]["removal_reason"], "未匹配")
        self.assertIsNone(view["files"][1].get("audit"))
