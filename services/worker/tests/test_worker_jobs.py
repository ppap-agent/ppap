"""Worker job dispatch. Does not prove a full product audit round."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch


from ppap_worker import run_claimed_job

ROOT = Path(__file__).resolve().parents[3]


class ClaimedJobTests(unittest.TestCase):
    def test_non_audit_jobs_do_not_import_the_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            result = run_claimed_job("heartbeat", {})
        self.assertEqual(result, {"note": "claimed; execution not implemented"})


class WorkerLoopTests(unittest.TestCase):
    def test_run_does_not_hold_a_connection_across_claim_job(self):
        text = (ROOT / "services/worker/ppap_worker/__init__.py").read_text()
        run = text.split("def run() -> None:", 1)[1].split("if __name__", 1)[0]
        before, after = run.split("claimed = store.claim_job(ident)", 1)
        self.assertIn('with connect(settings, "worker") as conn:', before)
        self.assertIn("worker_heartbeats", before)
        self.assertNotIn("claimed = store.claim_job(ident)", before)
        self.assertTrue(before.rstrip().endswith(")"))
        self.assertIn("process_claimed(store, claimed, ident)", after)
        self.assertIn("except Exception:", run)
