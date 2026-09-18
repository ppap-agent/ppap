"""Visible round presentation. Not S01 Live."""
from __future__ import annotations

import unittest

from ppap_core.display import finding_status, next_action, round_display


class DisplayTests(unittest.TestCase):
    def test_unknown_status_is_err_not_pass(self):
        self.assertEqual(finding_status(""), "ERR")
        self.assertEqual(finding_status("pass"), "PASS")
        self.assertEqual(finding_status("mystery"), "ERR")

    def test_references_are_not_visible_findings(self):
        display = round_display(
            "succeeded",
            [
                {"file_version_id": "t1", "role": "target"},
                {"file_version_id": "r1", "role": "reference"},
            ],
            [
                {
                    "file_version_id": "t1",
                    "sha256": "a" * 64,
                    "findings": [{"item": "rule-a", "status": "NG", "evidence": {"sources": [{"locator": "sheet://A1"}]}}],
                },
                {
                    "file_version_id": "r1",
                    "sha256": "b" * 64,
                    "findings": [{"item": "should-hide", "status": "PASS"}],
                },
            ],
        )
        self.assertEqual(display["execution_state"], "completed")
        self.assertEqual(len(display["visible_findings"]), 1)
        self.assertEqual(display["visible_findings"][0]["id"], "rule-a")
        self.assertEqual(display["visible_findings"][0]["status"], "NG")
        self.assertEqual(display["visible_findings"][0]["evidence_state"], "available")
        self.assertEqual(display["visible_findings"][0]["locator"], "sheet://A1")
        self.assertTrue(display["visible_findings"][0]["next_action"])
        self.assertEqual(display["coverage"]["reference_count"], 1)
        self.assertNotEqual(next_action("ERR"), "")


if __name__ == "__main__":
    unittest.main()
