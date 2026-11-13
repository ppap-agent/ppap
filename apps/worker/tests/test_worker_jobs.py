"""Worker job dispatch. Does not prove a full product audit round."""

from __future__ import annotations

import unittest

from worker import run_claimed_job


class ClaimedJobTests(unittest.TestCase):
    def test_unknown_job_kind_is_unsupported(self):
        result = run_claimed_job("heartbeat", {})
        self.assertEqual(result, {"error": "unsupported_job_kind", "note": "unsupported_job_kind"})
