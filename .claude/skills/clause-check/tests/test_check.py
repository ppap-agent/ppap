"""Offline smoke/regression tests for the lightweight clause review contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CHECK = SKILL / "scripts" / "check.py"
EXAMPLE = SKILL / "examples" / "X01" / "X01-1.review.json"


class ClauseReviewTests(unittest.TestCase):
    def run_check(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CHECK), str(path)], capture_output=True, text=True)

    def sample_review(self) -> dict:
        return json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def fixture(self, root: Path, review: dict, text: str | None = None) -> Path:
        clause = text or "都已签名并填写日期则通过，任一为空则不通过；批准日期不得早于编制日期；签字栏不存在则不通过。"
        (root / "rules.json").write_text(json.dumps({"title": "测试规则", "rules": [{"id": "X01:1", "category": "签字", "rule_to_check": clause}]}, ensure_ascii=False), encoding="utf-8")
        review["source"] = "rules.json#X01:1"
        path = root / "X01-1.review.json"
        path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        return path

    def test_example_generates_reports_and_excludes_false_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self.sample_review()
            source = json.loads((SKILL / "examples" / "X01" / "rules.json").read_text(encoding="utf-8"))["rules"][0]["rule_to_check"]
            path = self.fixture(root, review, source)
            result = self.run_check(path)
            self.assertEqual(0, result.returncode, result.stderr)
            report = (root / "X01-1.report.md").read_text(encoding="utf-8")
            data = json.loads((root / "X01-1.report.json").read_text(encoding="utf-8"))
            self.assertEqual("clause-check-report-v1", data["schema"])
            self.assertEqual(["X01:1#1", "X01:1#2.1"], [item["id"] for item in data["findings"]])
            self.assertEqual(1, len(data["excluded"]))
            self.assertIn("已排除的疑似问题（不进入问卷）", report)
            self.assertIn("语义仍须人工审阅", report)
            self.assertNotIn("签字栏不存在是否还能通过" + "？", report)

    def test_wrong_quote_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = self.sample_review()
            review["findings"][0]["quotes"] = ["七项满足即通过"]
            path = self.fixture(Path(tmp), review)
            result = self.run_check(path)
            self.assertEqual(1, result.returncode)
            self.assertIn("不属于所引用的原文", result.stderr)
            self.assertFalse((Path(tmp) / "X01-1.report.json").exists())

    def test_same_verdicts_are_not_a_discriminating_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = self.sample_review()
            review["findings"][0]["readings"][1]["verdict"] = "通过"
            path = self.fixture(Path(tmp), review)
            result = self.run_check(path)
            self.assertEqual(1, result.returncode)
            self.assertIn("判定没有差异", result.stderr)

    def test_stale_reports_removed_on_invalid_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self.sample_review()
            src = json.loads((SKILL / "examples" / "X01" / "rules.json").read_text(encoding="utf-8"))["rules"][0]["rule_to_check"]
            path = self.fixture(root, review, src)
            self.assertEqual(0, self.run_check(path).returncode)
            review["findings"][0]["question"] = "不是问句"
            path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(1, self.run_check(path).returncode)
            self.assertFalse((root / "X01-1.report.md").exists())
            self.assertFalse((root / "X01-1.report.json").exists())

    def test_required_conditions_and_stamp_are_not_a_conflict(self):
        """Gold negative witness: do not manufacture pass from a necessary condition."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clause = "七项全部满足才通过，否则不通过。签字与印章任一缺失，不通过。"
            review = {
                "schema": "clause-check-review-v1",
                "source": "rules.json#X01:1",
                "requirements": [
                    {"quote": "七项全部满足才通过", "role": "necessary", "scope": "七项", "meaning": "任一项缺失不得通过。"},
                    {"quote": "签字与印章任一缺失，不通过", "role": "prohibition", "scope": "供应商签署", "meaning": "签字和印章同时必需。"},
                ],
                "findings": [],
                "excluded": [{"quote": "七项全部满足才通过", "issue": "七项齐全即可通过", "reason": "才通过只规定必要条件；签章要求仍独立有效。"}],
            }
            path = self.fixture(root, review, clause)
            self.assertEqual(0, self.run_check(path).returncode)
            report = json.loads((root / "X01-1.report.json").read_text(encoding="utf-8"))
            self.assertEqual([], report["findings"])
            self.assertEqual(1, len(report["excluded"]))

    def test_questionnaire_uses_structured_report_and_rejects_stale_input(self):
        questionnaire = SKILL / "scripts" / "questionnaire.py"
        if not questionnaire.exists():
            self.skipTest("questionnaire.py is updated in the repository commit")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self.sample_review()
            src = json.loads((SKILL / "examples" / "X01" / "rules.json").read_text(encoding="utf-8"))["rules"][0]["rule_to_check"]
            path = self.fixture(root, review, src)
            self.assertEqual(0, self.run_check(path).returncode)
            plan = root / "plan.json"
            plan.write_text(
                json.dumps({"customer_id": "uaes", "rule_version": "测试条款 v1.0", "rulebook_version": "v1.0", "rulebook_revision": 0, "title": "测试问卷"}, ensure_ascii=False), encoding="utf-8"
            )
            output = root / "import.json"
            build = subprocess.run([sys.executable, str(questionnaire), "build", str(root), str(plan), str(output)], capture_output=True, text=True)
            self.assertEqual(0, build.returncode, build.stderr)
            cards = json.loads(output.read_text(encoding="utf-8"))["questions"]
            self.assertEqual(["X01:1#1", "X01:1#2.1"], [card["key"] for card in cards])
            review["findings"][0]["resolution"] = {"answer": "电子签名有效", "source": "客户测试答复"}
            path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            stale = subprocess.run([sys.executable, str(questionnaire), "build", str(root), str(plan), str(output)], capture_output=True, text=True)
            self.assertNotEqual(0, stale.returncode)
            self.assertIn("过期", stale.stderr + stale.stdout)
            self.assertEqual(0, self.run_check(path).returncode)
            updated = subprocess.run([sys.executable, str(questionnaire), "build", str(root), str(plan), str(output)], capture_output=True, text=True)
            self.assertEqual(0, updated.returncode, updated.stderr)
            self.assertEqual(["X01:1#2.1"], [card["key"] for card in json.loads(output.read_text(encoding="utf-8"))["questions"]])

    def test_resolution_preserved_for_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review = self.sample_review()
            src = json.loads((SKILL / "examples" / "X01" / "rules.json").read_text(encoding="utf-8"))["rules"][0]["rule_to_check"]
            review["findings"][0]["resolution"] = {"answer": "电子签名可以", "source": "客户答复，2026-11-21"}
            path = self.fixture(root, review, src)
            self.assertEqual(0, self.run_check(path).returncode)
            report = json.loads((root / "X01-1.report.json").read_text(encoding="utf-8"))
            self.assertEqual("电子签名可以", report["findings"][0]["resolution"]["answer"])
            self.assertIn("已答复", (root / "X01-1.report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
