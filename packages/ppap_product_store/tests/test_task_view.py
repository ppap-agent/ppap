import unittest
from copy import deepcopy

from ppap_product_store.task_view import task_round_view


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
