"""只证明验证器，不证明 PPAP 产品或真实 provider。"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.legacy_engine_gate import (GateError, batch_success, compare_results, digest,
                             engine_run, main, require_sha, response_data, version_probe)

SHA = "a" * 40
ORACLE = [{"classification": "structure", "item": "one", "status": "NG"},
          {"classification": "structure", "item": "two", "status": "PASS"}]
ENV = {"PPAP_LEGACY_ENGINE_URL": "https://engine.invalid/api/v1",
       "PPAP_LEGACY_VERSION_URL": "https://engine.invalid/version",
       "PPAP_LEGACY_ENGINE_TOKEN": "private-test-token",
       "PPAP_LEGACY_PROJECT_ID": "test-project",
       "PPAP_LIVE_SOURCE_URL": "https://objects.invalid/original.xlsx?signature=private"}


def case():
    return {"engine_git_sha": SHA, "max_wait_seconds": 1, "poll_seconds": 0.001,
            "files": [{"rule_id": "rule-1", "source_url_env": "PPAP_LIVE_SOURCE_URL",
                       "sha256": digest(b"fixture"), "expected": ORACLE}]}


class ResultTests(unittest.TestCase):
    def test_order_is_not_semantics(self):
        self.assertEqual(compare_results(list(reversed(ORACLE)), ORACLE)["checks"], 2)

    def test_ng_is_a_valid_proven_business_result(self):
        self.assertEqual(compare_results(ORACLE, ORACLE)["status_counts"]["NG"], 1)

    def test_empty_cannot_pass(self):
        with self.assertRaisesRegex(GateError, "empty_results"):
            compare_results([], ORACLE)

    def test_unknown_cannot_be_pass(self):
        rows = [dict(ORACLE[0], status="OK"), ORACLE[1]]
        with self.assertRaisesRegex(GateError, "unknown_result_status"):
            compare_results(rows, ORACLE)

    def test_missing_required_check(self):
        with self.assertRaisesRegex(GateError, "coverage"):
            compare_results(ORACLE[:1], ORACLE)

    def test_added_check_is_unreviewed(self):
        with self.assertRaisesRegex(GateError, "coverage"):
            compare_results(ORACLE + [dict(ORACLE[0], item="extra")], ORACLE)

    def test_duplicate_cannot_replace_missing_check(self):
        with self.assertRaisesRegex(GateError, "duplicate_result_key"):
            compare_results([ORACLE[0], ORACLE[0]], ORACLE)

    def test_wrong_status_fails(self):
        with self.assertRaisesRegex(GateError, "oracle_mismatch"):
            compare_results([dict(ORACLE[0], status="PASS"), ORACLE[1]], ORACLE)

    def test_duplicate_oracle_fails(self):
        with self.assertRaisesRegex(GateError, "duplicate_oracle_key"):
            compare_results(ORACLE, [ORACLE[0], ORACLE[0]])

    def test_empty_oracle_fails(self):
        with self.assertRaisesRegex(GateError, "nonempty_oracle"):
            compare_results(ORACLE, [])

    def test_required_locator_must_exist(self):
        with self.assertRaisesRegex(GateError, "required_locator_absent"):
            compare_results([ORACLE[0]], [dict(ORACLE[0], require_locator=True)])

    def test_optional_locator_not_imposed_on_every_format(self):
        self.assertEqual(compare_results([ORACLE[0]], [ORACLE[0]])["with_locator"], 0)

    def test_locator_supported(self):
        row = dict(ORACLE[0], evidence={"sources": [{"locator": "Sheet1!A2"}]})
        self.assertEqual(compare_results([row], [dict(ORACLE[0], require_locator=True)])["with_locator"], 1)

    def test_wrong_batch_identity(self):
        with self.assertRaisesRegex(GateError, "wrong_accepted_identity"):
            batch_success({"data": {"success": [{"taskId": "other"}], "failed": []}}, {"mine"}, "taskId")

    def test_partial_batch_not_success(self):
        with self.assertRaisesRegex(GateError, "batch_not_fully_accepted"):
            batch_success({"data": {"success": [], "failed": [{}]}}, {"mine"}, "taskId")

    def test_short_sha_is_not_release_identity(self):
        with self.assertRaises(GateError):
            require_sha("abcdef12")

    @patch("tools.legacy_engine_gate.fetch", return_value={"git_sha": "b" * 40})
    def test_wrong_artifact(self, _):
        with self.assertRaisesRegex(GateError, "artifact_mismatch"):
            version_probe("https://engine.invalid/version", SHA)

    def test_invalid_envelope(self):
        with self.assertRaises(GateError):
            response_data({"status": "completed"})


class FlowTests(unittest.TestCase):
    def exercise(self, mode):
        calls = []
        def fake(url, token="", payload=None, **kwargs):
            calls.append((url, payload))
            if url.endswith("/version"):
                return {"git_sha": SHA}
            if url.endswith("/audits/analysis"):
                return {"data": {"failed": [], "success": [{"auditId": payload[0]["auditId"],
                         "matchedRuleId": "wrong" if mode == "rule_changed" else "rule-1"}]}}
            if url.endswith("/tasks"):
                if mode == "submit_timeout":
                    raise GateError("transport_incomplete")
                return {"data": {"failed": [], "success": [{"taskId": payload[0]["taskId"],
                        "auditId": "wrong" if mode == "wrong_audit" else payload[0]["auditId"]}]}}
            return {"data": [{"taskId": "wrong" if mode == "wrong_id" else "live-task-unit-0",
                              "status": "failed" if mode == "failed" else "completed", "result": ORACLE}]}
        report = {"run_id": "unit"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ENV, clear=True), \
                patch("tools.legacy_engine_gate.fetch", side_effect=fake), \
                patch("tools.legacy_engine_gate.fetch_bytes", return_value=b"fixture"):
            try:
                engine_run(case(), report, Path(tmp))
                error = None
            except GateError as exc:
                error = exc.code
            private = Path(tmp) / "reconcile.local.json"
            return report, calls, error, private.exists()

    def test_single_submit_even_after_timeout(self):
        report, calls, error, state_exists = self.exercise("submit_timeout")
        self.assertEqual(error, "transport_incomplete")
        self.assertEqual(sum(u.endswith("/tasks") for u, p in calls), 1)
        self.assertEqual(report["task_invocations"], 1)
        self.assertTrue(state_exists)

    def test_success_is_one_analysis_and_one_submit(self):
        report, calls, error, _ = self.exercise("ok")
        self.assertIsNone(error)
        self.assertEqual(len([p for u, p in calls if p is not None]), 2)
        self.assertEqual(report["stage"], "complete")

    def test_matched_rule_change_never_auto_submitted(self):
        report, calls, error, _ = self.exercise("rule_changed")
        self.assertEqual(error, "rule_match_changed")
        self.assertFalse(any(u.endswith("/tasks") for u, p in calls))

    def test_wrong_accepted_source_rejected(self):
        self.assertEqual(self.exercise("wrong_audit")[2], "accepted_task_source_mismatch")

    def test_wrong_poll_identity_rejected(self):
        self.assertEqual(self.exercise("wrong_id")[2], "wrong_task_identity")

    def test_failed_task_is_not_empty_success(self):
        self.assertEqual(self.exercise("failed")[2], "engine_execution_failed")

    def test_source_mismatch_prevents_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ENV, clear=True), \
                patch("tools.legacy_engine_gate.fetch", return_value={"git_sha": SHA}) as fetch_mock, \
                patch("tools.legacy_engine_gate.fetch_bytes", return_value=b"different"):
            with self.assertRaisesRegex(GateError, "source_digest_mismatch"):
                engine_run(case(), {"run_id": "unit"}, Path(tmp))
            self.assertEqual(fetch_mock.call_count, 1)  # Version GET only.

    def test_missing_environment_is_not_skip_or_pass(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(case()))
            self.assertEqual(main(["--case", str(path), "--out", tmp]), 2)
            report = json.loads(output.getvalue())
            self.assertEqual(report["outcome"], "NOT_PROVEN")
            self.assertEqual(report["task_invocations"], 0)
            self.assertNotIn("private-test-token", output.getvalue())

    @patch("tools.legacy_engine_gate.fetch", side_effect=RuntimeError("secret=never-print"))
    def test_unexpected_error_text_is_not_retained(self, _):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ENV, clear=True), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(case()))
            self.assertEqual(main(["--case", str(path), "--out", tmp]), 2)
            self.assertNotIn("never-print", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["product_story_outcome"], "NOT_PROVEN")


if __name__ == "__main__":
    unittest.main()
