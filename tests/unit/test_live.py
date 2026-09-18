"""Tool tests only. These tests do not prove live product behavior."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.live import NotProven, check_findings, origin, probe, required, same_origin, normalized_findings, NoRedirect

ROOT = Path(__file__).resolve().parents[2]


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.oracle = {"source_sha256": "a" * 64, "findings": {"R1": {"status": "NG", "locator": "Sheet1!B2"}}}
        self.actual = [{"id": "R1", "status": "NG", "evidence_state": "available", "locator": "Sheet1!B2", "source_sha256": "a" * 64}]

    def test_valid_semantics(self):
        check_findings(self.actual, self.oracle)

    def test_empty_result_is_not_success(self):
        with self.assertRaises(NotProven): check_findings([], self.oracle)

    def test_missing_oracle_is_not_success(self):
        with self.assertRaises(NotProven): check_findings(self.actual, {})

    def test_duplicate_rule_is_rejected(self):
        with self.assertRaises(NotProven): check_findings(self.actual * 2, self.oracle)

    def test_missing_rule_is_rejected(self):
        self.oracle["findings"]["R2"] = {"status": "PASS", "locator": None}
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_extra_rule_is_rejected_for_selected_case(self):
        other = dict(self.actual[0], id="R2")
        with self.assertRaises(NotProven): check_findings(self.actual + [other], self.oracle)

    def test_execution_completed_is_not_pass(self):
        self.actual[0]["status"] = "completed"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_silent_pass_mapping_rejected(self):
        self.actual[0]["status"] = "PASS"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_wrong_source_rejected(self):
        self.actual[0]["source_sha256"] = "b" * 64
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_wrong_locator_rejected(self):
        self.actual[0]["locator"] = "Sheet1!B3"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_unavailable_evidence_rejected(self):
        self.actual[0]["evidence_state"] = "missing"
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_known_locator_gap_must_be_disclosed(self):
        self.oracle["findings"]["R1"]["locator"] = None
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)
        self.actual[0]["evidence_state"] = "missing"
        check_findings(self.actual, self.oracle)

    def test_unspecified_evidence_is_not_disclosed_gap(self):
        del self.oracle["findings"]["R1"]["locator"]
        with self.assertRaises(NotProven): check_findings(self.actual, self.oracle)

    def test_row_order_not_a_contract(self):
        self.oracle["findings"]["R2"] = {"status": "ERR", "locator": None}
        other = {"id": "R2", "status": "ERR", "evidence_state": "missing"}
        check_findings([other] + self.actual, self.oracle)
        check_findings(self.actual + [other], self.oracle)

    def test_history_comparison_ignores_order(self):
        rows = [{"id": "B", "status": "PASS"}, {"id": "A", "status": "NG"}]
        self.assertEqual(normalized_findings(rows), normalized_findings(rows[::-1]))

    def test_probe_does_not_follow_redirect(self):
        with self.assertRaises(NotProven):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://else.test")

    def test_implicit_https_port_is_equivalent(self):
        same_origin("https://example.test:443/round", "https://example.test")

    def test_cross_origin_rejected(self):
        with self.assertRaises(NotProven): same_origin("https://else.test", "https://example.test")

    def test_credential_in_url_rejected(self):
        with self.assertRaises(NotProven): origin("https://user:secret@example.test")

    def test_local_file_url_rejected(self):
        with self.assertRaises(NotProven): origin("file:///tmp/private")

    def test_missing_configuration_not_success(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(NotProven): required("PPAP_WEB_URL")

    def test_cli_missing_target_nonzero_secret_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PPAP_")}
            result = subprocess.run([sys.executable, "tools/live.py", "audit", "--output-dir", directory], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            records = list(Path(directory).glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text())
            self.assertEqual(record["outcome"], "NOT_PROVEN")
            self.assertEqual(record["kind"], "product_child_proof")
            self.assertNotIn("submit_invocations", record["evidence"])
            self.assertEqual(record["reason"], "missing_configuration:PPAP_WEB_URL")


if __name__ == "__main__": unittest.main()
