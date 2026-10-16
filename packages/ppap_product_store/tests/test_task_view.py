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


class TaskRoundViewTests(unittest.TestCase):
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
        finding = {"item_key": "version-a:engine:1", "status": "NG", "locator": {"file_version_id": "version-b"}, "review": None}
        round["findings"] = [finding]
        view = round_read_view(round, {key: {"id": key} for key in ("version-a", "version-b")})
        self.assertEqual(view["unassigned_findings"], [finding])
        self.assertTrue(all(not row["audit"]["unmapped_findings"] for row in view["files"]))

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
