"""Worker job dispatch. Does not prove a full product audit round."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import tests.pathsetup  # noqa: F401

from ppap_worker import run_claimed_job


class ClaimedJobTests(unittest.TestCase):
    def test_non_audit_jobs_do_not_import_the_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            result = run_claimed_job("heartbeat", {})
        self.assertEqual(result, {"note": "claimed; execution not implemented"})
